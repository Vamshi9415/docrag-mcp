"""Image processor — OCR via pytesseract."""

from __future__ import annotations

import os
import logging
import uuid
from io import BytesIO
from datetime import datetime
from typing import List

from PIL import Image

from mcp_server.core.config import OCR_AVAILABLE, TEMP_FILES_PATH
from mcp_server.core.schemas import ExtractedImage

logger = logging.getLogger("mcp_server.processors.image")


class ImageOCRProcessor:
    """Extract text from PNG / JPEG images using pytesseract OCR."""

    @staticmethod
    async def process_image_file(
        file_content: bytes, file_path: str, request_id: str
    ) -> List[ExtractedImage]:
        images: List[ExtractedImage] = []
        if not OCR_AVAILABLE:
            logger.error("Pytesseract not available but image processing requested")
            return images

        import pytesseract

        temp_path = os.path.join(TEMP_FILES_PATH, f"{request_id}_{uuid.uuid4().hex}.png")

        try:
            pil = Image.open(BytesIO(file_content))
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            pil.save(temp_path, "PNG")
            width, height = pil.size

            try:
                ocr_data = pytesseract.image_to_data(
                    pil, output_type=pytesseract.Output.DATAFRAME, lang="eng"
                )
                ocr_data = ocr_data[ocr_data.conf > 0]
                if not ocr_data.empty:
                    text = " ".join(ocr_data["text"].dropna().astype(str))
                    conf = ocr_data["conf"].mean() / 100.0

                    images.append(ExtractedImage(
                        image_path=temp_path,
                        ocr_text=text.strip(),
                        metadata={
                            "source": file_path,
                            "extraction_method": "pytesseract",
                            "image_dimensions": f"{width}x{height}",
                            "processing_timestamp": datetime.now().isoformat(),
                            "mean_confidence_score": f"{conf:.2f}",
                        },
                        confidence=conf,
                    ))
                    logger.info(f"OCR extracted {len(text)} chars from image")
                else:
                    logger.warning("No text with sufficient confidence from OCR")
            except pytesseract.TesseractNotFoundError:
                logger.error("Tesseract is not installed or not in PATH")
            except Exception as exc:
                logger.error(f"OCR failed: {exc}")

        except Exception as exc:
            logger.error(f"Image processing failed for {file_path}: {exc}")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

        return images
