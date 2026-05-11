"""
payment_verifier.py
-------------------
Advanced OCR-based payment screenshot verification engine (Phase 1).
Uses Multi-OCR (EasyOCR + Pytesseract) and fuzzy logic for high accuracy.
"""

import os
import re
import cv2
import numpy as np
import logging
from PIL import Image, ImageEnhance
import pytesseract
import easyocr
from rapidfuzz import fuzz, process

# ── Logging Setup ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PaymentVerifier")

# ── Global OCREngines ──────────────────────────────────────────────────────
try:
    # Initialize EasyOCR reader (English only for speed)
    # Note: gpu=False by default for stability on varied systems
    reader = easyocr.Reader(['en'], gpu=False)
    EASYOCR_AVAILABLE = True
except Exception as e:
    logger.error(f"EasyOCR initialization failed: {e}")
    EASYOCR_AVAILABLE = False

# ── Constants & Keywords ───────────────────────────────────────────────────
SUCCESS_KEYWORDS = [
    "success", "successful", "payment successful", "paid", "approved",
    "completed", "done", "debited", "transaction successful",
    "payment done", "amount paid", "money sent", "sent successfully",
    "transferred", "payment complete", "payment approved", "txn success",
    "transaction id", "utr", "upi ref", "ref no"
]

# Improved patterns to avoid catching years like 2026
AMOUNT_PATTERNS = [
    # Patterns with currency symbols (including common OCR misreads of ₹ as 7, T, :, etc.)
    r"(?:₹|Rs\.?|INR|Total|Amt|Amount|[7TF\:])\s*[:\-]?\s*(\d{1,6}(?:[.,]\d{2})?)",
    # Patterns with "paid" or "sent" context
    r"(\d{1,6}(?:\.\d{2})?)\s*(?:paid|sent|successfully|debited|transferred)",
    # Amount preceded by "Paid to" or similar
    r"(?:paid to|transfer to)\s+.*?\s+(\d{1,6}(?:[.,]\d{2})?)",
]

# Patterns to explicitly IGNORE (like years)
IGNORE_PATTERNS = [
    r"\b202[4-9]\b", # Ignore years 2024-2029
    r"\b\d{2}:\d{2}\b", # Ignore times HH:MM
    r"\b\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b" # Ignore dates
]

UPI_ID_PATTERN = r"[a-zA-Z0-9.\-_+]+@[a-zA-Z0-9.\-]+"
TXN_ID_PATTERNS = [
    r"(?:UTR|UPI|Ref|Txn|Transaction\s*ID)[:\s#]*([A-Z0-9]{8,22})",
    r"\b(\d{12})\b", # Common 12-digit UTR
    r"\b(T\d{20,25})\b", # PhonePe style transaction IDs
]

# ── Image Preprocessing ────────────────────────────────────────────────────
class ImagePreprocessor:
    @staticmethod
    def preprocess_image(image_path):
        """
        Loads an image and applies a series of preprocessing steps.
        Returns a PIL Image object.
        """
        logger.info(f"Preprocessing image: {image_path}")
        img = Image.open(image_path).convert("RGB")

        # Convert to OpenCV format for advanced processing
        cv_img = np.array(img)
        cv_img = cv_img[:, :, ::-1].copy() # Convert RGB to BGR

        # Grayscale
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

        # Noise Reduction (Gaussian Blur)
        denoised = cv2.GaussianBlur(gray, (5, 5), 0)

        # Contrast adjustment (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        contrast_enhanced = clahe.apply(denoised)

        # Skew correction (rotation) - basic implementation
        coords = np.column_stack(np.where(contrast_enhanced > 0))
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        (h, w) = contrast_enhanced.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(contrast_enhanced, M, (w, h),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

        # Convert back to PIL Image
        processed_img = Image.fromarray(rotated)
        logger.info("Image preprocessing complete.")
        return processed_img

    @staticmethod
    def get_variations(pil_image):
        """Generates multiple image variations from a PIL Image to improve OCR chances."""
        variations = []
        
        # 1. Original (processed)
        variations.append(("original_pil", pil_image))

        # 2. Binarized (Otsu's)
        cv_img = np.array(pil_image)
        _, thresh = cv2.threshold(cv_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variations.append(("thresh", Image.fromarray(thresh)))

        # 3. Sharpened
        sharpened_pil = ImageEnhance.Sharpness(pil_image).enhance(2.0)
        variations.append(("sharpened", sharpened_pil))

        logger.debug(f"Generated {len(variations)} image variations.")
        return variations

# ── OCR Engine Wrapper ─────────────────────────────────────────────────────
class OCREngine:
    def __init__(self):
        # psm modes: 3 (default, auto), 6 (single block), 11 (sparse text), 12 (sparse text, OSD)
        self.tesseract_configs = [
            r'--oem 3 --psm 3',
            r'--oem 3 --psm 6',
            r'--oem 3 --psm 11',
            r'--oem 3 --psm 12',
        ]

    def extract_text(self, pil_image):
        """Combines EasyOCR and Tesseract results for robustness from a PIL Image."""
        combined_text_parts = []
        
        # Try EasyOCR first
        if EASYOCR_AVAILABLE:
            try:
                logger.info("Attempting OCR with EasyOCR.")
                # EasyOCR expects a filepath or numpy array, convert PIL to numpy
                img_np = np.array(pil_image)
                results = reader.readtext(img_np, detail=0)
                # Ensure results is a list of strings
                if isinstance(results, list):
                    easyocr_text = " ".join([str(r) for r in results])
                    combined_text_parts.append(easyocr_text)
                    logger.debug(f"EasyOCR extracted: {easyocr_text[:100]}...")
            except Exception as e:
                logger.error(f"EasyOCR failed: {e}")

        # Try Tesseract variations
        for i, config in enumerate(self.tesseract_configs):
            try:
                logger.info(f"Attempting OCR with Tesseract (config: {config}).")
                text = str(pytesseract.image_to_string(pil_image, config=config))
                combined_text_parts.append(text)
                logger.debug(f"Tesseract (config {i}) extracted: {text[:100]}...")
            except Exception as e:
                logger.error(f"Tesseract (config: {config}) failed: {e}")

        full_text = "\n".join([str(p) for p in combined_text_parts]).strip()
        logger.info(f"Combined OCR text length: {len(full_text)}")
        return full_text

# ── Analysis Engine ────────────────────────────────────────────────────────
# ── Analysis Engine ────────────────────────────────────────────────────────
class PaymentAnalyzer:
    def __init__(self, raw_text):
        self.text = raw_text.lower()
        self.original_text = raw_text
        logger.debug(f"Initializing PaymentAnalyzer with text length: {len(raw_text)}")

    def check_success(self):
        """Uses fuzzy matching to detect success keywords."""
        logger.debug("Checking for success keywords.")
        for kw in SUCCESS_KEYWORDS:
            if kw in self.text:
                logger.debug(f"Direct success keyword match: {kw}")
                return True
            for line in self.text.split('\n'):
                if fuzz.partial_ratio(kw, line) > 85:
                    logger.debug(f"Fuzzy success keyword match: {kw} in line: {line}")
                    return True
        logger.debug("No success keywords found.")
        return False

    def extract_amount(self, expected_hint=None):
        """Extracts the most likely payment amount, ignoring dates and times."""
        logger.debug(f"Extracting amount. Expected hint: {expected_hint}")
        
        # Pre-filter text to remove common false positives (like years 2026)
        filtered_text = self.original_text
        for ignore_pat in IGNORE_PATTERNS:
            filtered_text = re.sub(ignore_pat, " [IGNORE] ", filtered_text)

        amounts = []
        for pat in AMOUNT_PATTERNS:
            # Search in the filtered text first
            matches = re.findall(pat, filtered_text, re.IGNORECASE)
            for m in matches:
                try:
                    if isinstance(m, tuple):
                        m = next((item for item in m if item), None)
                        if not m: continue
                    
                    cleaned = m.replace(",", "").strip()
                    if cleaned.endswith('.'):
                        cleaned = cleaned[:-1]
                    
                    # Ignore numbers that are too long (likely UTR or Txn IDs)
                    if len(cleaned) > 8:
                        continue

                    if re.fullmatch(r'\d+(\.\d{1,2})?', cleaned):
                        amounts.append(float(cleaned))
                except ValueError:
                    continue
        
        if amounts:
            # CRITICAL: If any detected amount matches our expected hint, prioritize it!
            if expected_hint is not None:
                hint_val = float(expected_hint)
                for a in amounts:
                    if abs(a - hint_val) < 0.1:
                        logger.info(f"Found exact expected amount match: {a}")
                        return a

            # Fallback to most common
            most_common_amount = max(set(amounts), key=amounts.count)
            logger.info(f"Detected amounts: {amounts}. Choosing most common: {most_common_amount}")
            return most_common_amount
        return None

    def extract_upi(self):
        """Extracts UPI IDs from the text."""
        logger.debug("Extracting UPI ID.")
        matches = re.findall(UPI_ID_PATTERN, self.original_text, re.IGNORECASE)
        if matches:
            logger.info(f"Detected UPI ID: {matches[0]}")
            return matches[0]
        logger.debug("No UPI ID detected.")
        return None

    def extract_txn_id(self):
        """Extracts transaction ID or UTR number."""
        logger.debug("Extracting transaction ID.")
        for pat in TXN_ID_PATTERNS:
            match = re.search(pat, self.original_text, re.IGNORECASE)
            if match:
                logger.info(f"Detected transaction ID: {match.group(1)}")
                return match.group(1)
        logger.debug("No transaction ID detected.")
        return None

# ── Main Entry Point ───────────────────────────────────────────────────────
def verify_payment_screenshot(filepath, expected_amount, expected_upi_id):
    """
    Analyzes a screenshot and returns a comprehensive result dictionary.
    """
    logger.info(f"Starting verification for {filepath}")
    
    result = {
        "confidence": 0,
        "status": "failed",
        "ocr_text": "",
        "amount_detected": None,
        "upi_id_detected": None,
        "txn_id_detected": None,
        "success_keyword_found": False,
        "error": None
    }

    try:
        # 1. Image Preprocessing
        preprocessed_image = ImagePreprocessor.preprocess_image(filepath)
        if preprocessed_image is None:
            result["error"] = "Failed to preprocess image."
            result["status"] = "manual_review"
            logger.error(result["error"])
            return result

        # 2. OCR Extraction
        ocr_engine = OCREngine()
        raw_text = ocr_engine.extract_text(preprocessed_image)
        result["ocr_text"] = raw_text
        
        if not raw_text.strip():
            result["error"] = "No text detected in screenshot."
            result["status"] = "manual_review"
            logger.warning(result["error"])
            return result

        # 3. Analysis
        analyzer = PaymentAnalyzer(raw_text)
        
        success_found = analyzer.check_success()
        detected_amount = analyzer.extract_amount(expected_hint=expected_amount)
        detected_upi = analyzer.extract_upi()
        detected_txn = analyzer.extract_txn_id()

        result["success_keyword_found"] = success_found
        result["amount_detected"] = str(detected_amount) if detected_amount else None
        result["upi_id_detected"] = detected_upi
        result["txn_id_detected"] = detected_txn

        logger.info(f"OCR Results: Success={success_found}, Amount={detected_amount}, UPI={detected_upi}, Txn={detected_txn}")

        # 4. Confidence Scoring
        score = 0
        
        # 4a. Success keywords are vital
        if success_found:
            score += 30
            
        # 4b. Amount verification (Strict)
        if detected_amount is not None:
            # Check if it matches expected amount EXACTLY (or within small range)
            if abs(detected_amount - float(expected_amount)) < 0.1: # Strict match for 100% confidence path
                score += 50
                logger.debug(f"Score +50 for EXACT matching amount ({detected_amount}).")
            elif abs(detected_amount - float(expected_amount)) < 2.0:
                score += 30
                logger.debug(f"Score +30 for close matching amount.")
            else:
                # If detected amount is very different (like 2026 vs 10), penalize
                score -= 20
                logger.debug(f"Score -20 for mismatched amount ({detected_amount} vs {expected_amount}).")
        
        # 4c. UPI ID verification
        if detected_upi:
            if expected_upi_id and (expected_upi_id.lower() in raw_text.lower() or 
               fuzz.partial_ratio(expected_upi_id.lower(), detected_upi.lower()) > 85):
                score += 20
            else:
                score += 5

        # 4d. Transaction ID presence (high weight for PhonePe style IDs)
        if detected_txn:
            if re.match(r'T\d{20,}', str(detected_txn)): # PhonePe style
                score += 15
            else:
                score += 10

        # Normalize score
        result["confidence"] = max(0, min(score, 100))

        # 5. Status Determination (Strict)
        # To get "Verified" (Green), we now require:
        # 1. Confidence >= 90
        # 2. Success keywords found
        # 3. Exact or very close amount match
        if result["confidence"] >= 90 and success_found and detected_amount is not None and abs(detected_amount - float(expected_amount)) < 1.0:
            result["status"] = "verified"
            logger.info(f"Payment VERIFIED with high confidence ({result['confidence']}).")
        # To get "Manual Review" (Yellow), we require:
        # 1. Confidence >= 50
        # 2. OR Transaction ID found (even if amount failed OCR)
        elif result["confidence"] >= 50 or (detected_txn and success_found):
            result["status"] = "manual_review"
            logger.info(f"Payment requires MANUAL REVIEW (confidence: {result['confidence']}).")
        else:
            result["status"] = "failed"
            logger.warning(f"Payment FAILED verification (confidence: {result['confidence']}).")

        logger.info(f"Verification complete: Score={result['confidence']}, Status={result['status']}")
        
    except Exception as e:
        logger.exception(f"Verification process encountered an unexpected error for {filepath}.")
        result["error"] = str(e)
        result["status"] = "manual_review" # Fallback to manual review on unexpected errors

    return result

def generate_upi_link(upi_id: str, amount: float, name: str = "Event") -> str:
    from urllib.parse import quote
    safe_name = quote(name[:30])
    safe_upi = quote(upi_id)
    return f"upi://pay?pa={safe_upi}&pn={safe_name}&am={amount:.2f}&cu=INR&tn=EventFee"
