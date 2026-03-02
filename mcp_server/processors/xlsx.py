"""XLSX processor — multi-sheet table extraction with quality scoring.

Detects table regions inside each sheet, scores header quality, and
optionally performs cross-sheet relationship analysis.
"""

from __future__ import annotations

import io
import re
import logging
from typing import Any, Dict, List, Tuple

import pandas as pd

from mcp_server.core.schemas import ExtractedTable

logger = logging.getLogger("mcp_server.processors.xlsx")


class EnhancedXLSXTableExtractor:
    """Extract and format tables from XLSX workbooks."""

    @staticmethod
    def extract_tables_from_xlsx(file_content: bytes) -> List[ExtractedTable]:
        tables: List[ExtractedTable] = []

        try:
            xls = pd.ExcelFile(io.BytesIO(file_content), engine="openpyxl")

            for sheet_name in xls.sheet_names:
                try:
                    df = xls.parse(sheet_name, header=None, dtype=str).fillna("")
                    if df.empty:
                        continue

                    header_row, quality = EnhancedXLSXTableExtractor._detect_header(df)
                    if header_row >= 0:
                        headers = [str(h).strip() or f"Col_{i}" for i, h in enumerate(df.iloc[header_row])]
                        df = df.iloc[header_row + 1:].reset_index(drop=True)
                        df.columns = headers[:len(df.columns)]
                    else:
                        headers = [f"Col_{i}" for i in range(len(df.columns))]
                        df.columns = headers

                    # Remove empty rows
                    df = df[df.apply(lambda r: any(str(v).strip() for v in r), axis=1)]
                    if df.empty:
                        continue

                    content = EnhancedXLSXTableExtractor._format_table(df, sheet_name, quality)
                    metadata = EnhancedXLSXTableExtractor._extract_metadata(df, sheet_name, len(tables) + 1)

                    tables.append(ExtractedTable(
                        content=content,
                        table_type="xlsx",
                        location=f"Sheet: {sheet_name}",
                        metadata=metadata,
                    ))

                except Exception as exc:
                    logger.warning(f"Failed to process sheet '{sheet_name}': {exc}")

            # Cross-sheet analysis
            cross = EnhancedXLSXTableExtractor._cross_sheet_analysis(tables)
            if cross:
                tables.append(cross)

        except Exception as exc:
            logger.error(f"XLSX extraction failed: {exc}")

        return tables

    # ------------------------------------------------------------------
    # Header detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_header(df: pd.DataFrame) -> Tuple[int, float]:
        """Return (header_row_index, quality_score)."""
        best_row, best_score = -1, 0.0

        for idx in range(min(10, len(df))):
            row = df.iloc[idx]
            non_empty = sum(1 for v in row if str(v).strip())
            if non_empty < 2:
                continue

            unique = len(set(str(v).strip().lower() for v in row if str(v).strip()))
            uniqueness = unique / max(non_empty, 1)

            text_ratio = sum(
                1 for v in row
                if str(v).strip() and not str(v).replace(".", "").replace("-", "").isdigit()
            ) / max(non_empty, 1)

            score = (uniqueness * 0.5 + text_ratio * 0.3 + (non_empty / len(row)) * 0.2)
            if score > best_score:
                best_score = score
                best_row = idx

        return best_row, round(best_score, 2)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_table(df: pd.DataFrame, sheet_name: str, quality: float) -> str:
        lines: List[str] = [f"=== SHEET: {sheet_name} ==="]
        lines.append(f"DIMENSIONS: {df.shape[0]} rows × {df.shape[1]} columns")
        lines.append(f"HEADER QUALITY: {quality:.0%}")
        lines.append("")

        headers = list(df.columns)
        header_row = " | ".join(f"{str(h)[:15]:15s}" for h in headers)
        lines.append(f"HEADERS: {header_row}")
        lines.append("-" * 80)

        max_rows = min(20, len(df))
        for idx in range(max_rows):
            vals = []
            for v in df.iloc[idx]:
                vs = str(v)[:15] if pd.notna(v) and v != "" else ""
                vals.append(f"{vs:15s}")
            lines.append(f"ROW_{idx + 1:2d}: {' | '.join(vals)}")

        if len(df) > max_rows:
            lines.append(f"... [{len(df) - max_rows} more rows]")

        lines.append("")
        lines.append("DATA ANALYSIS:")
        for col in df.columns:
            col_data = df[col]
            non_empty = col_data[col_data != ""].count()
            unique_vals = col_data[col_data != ""].nunique()
            analysis = f"  {str(col)[:20]}: {non_empty} values, {unique_vals} unique"
            samples = col_data[col_data != ""].head(3).tolist()
            if samples:
                analysis += f" (samples: {', '.join(str(v)[:10] for v in samples)})"
            lines.append(analysis)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_metadata(df: pd.DataFrame, sheet_name: str, table_num: int) -> Dict[str, Any]:
        if df.empty:
            return {"error": "Empty dataframe"}

        metadata: Dict[str, Any] = {
            "sheet_name": sheet_name,
            "table_number": table_num,
            "dimensions": df.shape,
            "extraction_method": "enhanced_xlsx_processing",
            "processing_timestamp": pd.Timestamp.now().isoformat(),
        }

        data_types: Dict[str, str] = {}
        for col in df.columns:
            col_data = df[col][df[col] != ""]
            if col_data.empty:
                data_types[str(col)] = "empty"
            else:
                numeric_count = sum(
                    1 for val in col_data
                    if str(val).replace(".", "").replace("-", "").isdigit()
                )
                if numeric_count / len(col_data) > 0.8:
                    data_types[str(col)] = "numeric"
                elif any(kw in str(col).lower() for kw in ("date", "time", "created", "modified")):
                    data_types[str(col)] = "datetime"
                else:
                    data_types[str(col)] = "text"
        metadata["column_types"] = data_types

        total_cells = df.shape[0] * df.shape[1]
        non_empty = sum(1 for col in df.columns for v in df[col] if v != "")
        metadata["data_density"] = non_empty / total_cells if total_cells else 0
        metadata["non_empty_cells"] = non_empty

        all_text = " ".join(str(v) for col in df.columns for v in df[col] if v != "")
        url_re = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+\.[^\s<>"\']+')
        urls_found = url_re.findall(all_text)
        metadata["urls_found"] = len(urls_found)
        metadata["contains_urls"] = len(urls_found) > 0

        return metadata

    # ------------------------------------------------------------------
    # Cross-sheet analysis
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # CSV support
    # ------------------------------------------------------------------

    @staticmethod
    def extract_tables_from_csv(file_content: bytes) -> List[ExtractedTable]:
        """Parse CSV bytes into ExtractedTable(s)."""
        tables: List[ExtractedTable] = []
        try:
            text = file_content.decode("utf-8", errors="replace")
            df = pd.read_csv(io.StringIO(text), dtype=str).fillna("")
            if df.empty:
                return tables

            content = EnhancedXLSXTableExtractor._format_table(df, "CSV", 1.0)
            metadata = EnhancedXLSXTableExtractor._extract_metadata(df, "CSV", 1)

            tables.append(ExtractedTable(
                content=content,
                table_type="csv",
                location="CSV file",
                metadata=metadata,
            ))
        except Exception as exc:
            logger.error(f"CSV extraction failed: {exc}")
        return tables

    @staticmethod
    def _cross_sheet_analysis(tables: List[ExtractedTable]) -> ExtractedTable | None:
        try:
            sheet_names = [t.metadata.get("sheet_name", "") for t in tables if t.metadata.get("sheet_name")]
            unique_sheets = set(sheet_names)
            if len(unique_sheets) <= 1:
                return None

            parts = [
                "=== CROSS-SHEET ANALYSIS ===",
                f"WORKBOOK CONTAINS: {len(unique_sheets)} sheets",
                f"SHEET NAMES: {', '.join(unique_sheets)}",
                "",
            ]

            all_content = " ".join(t.content for t in tables)
            url_re = re.compile(r'https?://[^\s<>"\']+')
            all_urls = url_re.findall(all_content)
            if all_urls:
                unique_urls = list(set(all_urls))
                parts.append(f"URLS FOUND ACROSS SHEETS: {len(all_urls)} total")
                parts.extend(f"  {u}" for u in unique_urls[:5])
                if len(unique_urls) > 5:
                    parts.append(f"  ... and {len(unique_urls) - 5} more")

            return ExtractedTable(
                content="\n".join(parts),
                table_type="xlsx_cross_analysis",
                location="Cross-sheet analysis",
                metadata={
                    "analysis_type": "cross_sheet_relationships",
                    "sheets_analyzed": list(unique_sheets),
                },
            )
        except Exception as exc:
            logger.warning(f"Cross-sheet analysis failed: {exc}")
            return None
