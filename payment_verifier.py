"""
payment_verifier.py
-------------------
OCR-based payment screenshot verification engine.

Uses pytesseract + OpenCV to extract text from UPI payment screenshots
and compute a confidence score (0–100) for auto-verification.

Scoring rubric:
  +35  Success keyword found (success / successful / payment complete)
  +30  Amount matches expected (within ±1 rupee)
  +20  UPI ID found in text
  +10  Transaction ID found (numeric ref / UPI UTR)
  +5   Image is valid and not too small
  ───
  100  Maximum

Decision thresholds:
  ≥ 75  → verified (auto-approve)
  40-74 → manual_review
  < 40  → failed
"""

import os
import re
import traceback
import logging

# ── Logging ────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Optional imports (graceful fallback if tesseract not installed) ────────
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
    logger.info("pytesseract available — OCR mode active")
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("pytesseract not available — all payments will go to manual review")

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("opencv-python not available — skipping image preprocessing")


# ── Constants ───────────────────────────────────────────────────────────────
SUCCESS_KEYWORDS = [
    "success", "successful", "payment successful", "paid", "approved",
    "completed", "done", "debit", "debited", "transaction successful",
    "payment done", "amount paid", "money sent", "sent successfully",
    "transferred", "payment complete", "payment approved",
]

MIN_IMAGE_WIDTH  = 100   # px
MIN_IMAGE_HEIGHT = 100   # px
MAX_IMAGE_BYTES  = 10 * 1024 * 1024   # 10 MB


# ── Image preprocessing for better OCR ─────────────────────────────────────
def _preprocess_image(filepath: str):
    """
    Load image and apply preprocessing to improve OCR accuracy.
    Returns a PIL Image ready for pytesseract.
    """
    if not CV2_AVAILABLE:
        return Image.open(filepath).convert("RGB")

    img = cv2.imread(filepath)
    if img is None:
        raise ValueError(f"Could not read image at {filepath}")

    # Upscale small images
    h, w = img.shape[:2]
    if w < 800:
        scale = 800 / w
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Adaptive threshold to handle varying backgrounds
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2
    )

    # Denoise
    denoised = cv2.fastNlMeansDenoising(thresh, h=10)

    pil_img = Image.fromarray(denoised)
    return pil_img


# ── Text extraction ──────────────────────────────────────────────────────────
def _extract_text(filepath: str) -> str:
    """Extract all text from the image using pytesseract."""
    if not TESSERACT_AVAILABLE:
        return ""

    try:
        pil_img = _preprocess_image(filepath)
        # Use PSM 6 (assume uniform block of text) for receipts
        custom_config = r"--oem 3 --psm 6"
        text = pytesseract.image_to_string(pil_img, config=custom_config)
        logger.debug(f"OCR raw text:\n{text}")
        return text
    except Exception as e:
        logger.error(f"OCR extraction failed: {e}\n{traceback.format_exc()}")
        return ""


# ── Field extractors ─────────────────────────────────────────────────────────
def _find_success_keyword(text: str) -> bool:
    """Check if any success-related keyword is in the OCR text."""
    lower = text.lower()
    return any(kw in lower for kw in SUCCESS_KEYWORDS)


def _extract_amount(text: str) -> str | None:
    """
    Find the largest currency amount in the text.
    Patterns: ₹500, Rs. 500, 500.00, INR 500
    """
    patterns = [
        r"(?:₹|Rs\.?\s*|INR\s*)(\d{1,6}(?:[.,]\d{0,2})?)",
        r"(\d{1,6}(?:\.\d{1,2})?)\s*(?:/-|rupees?|inr)",
        r"(?:amount|amt)[:\s]+(?:₹|Rs\.?\s*)?(\d{1,6}(?:\.\d{1,2})?)",
    ]
    found_amounts = []
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        for m in matches:
            cleaned = m.replace(",", "")
            try:
                found_amounts.append(float(cleaned))
            except ValueError:
                pass

    if found_amounts:
        return str(max(found_amounts))  # return largest amount found
    return None


def _extract_upi_id(text: str) -> str | None:
    """Find UPI ID pattern (anything@anything) in OCR text."""
    pattern = r"[a-zA-Z0-9.\-_+]+@[a-zA-Z0-9.\-]+"
    matches = re.findall(pattern, text)
    # Filter out emails that look like real email addresses (multi-dot domains)
    upi_candidates = [m for m in matches if "." not in m.split("@")[1] or
                      m.split("@")[1].count(".") == 1]
    return upi_candidates[0] if upi_candidates else None


def _extract_transaction_id(text: str) -> str | None:
    """
    Find UPI transaction reference / UTR number.
    Google Pay / PhonePe / Paytm usually show a 12–16 digit numeric ref.
    """
    patterns = [
        r"(?:UTR|UPI|Ref|Transaction\s*(?:ID|No)|txn)[:\s#]*([A-Z0-9]{8,20})",
        r"\b(\d{12,16})\b",  # bare long numeric
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# ── Image validation ─────────────────────────────────────────────────────────
def _validate_image(filepath: str) -> tuple[bool, str]:
    """
    Basic image validity check.
    Returns (is_valid, reason).
    """
    if not os.path.exists(filepath):
        return False, "File not found"

    file_size = os.path.getsize(filepath)
    if file_size > MAX_IMAGE_BYTES:
        return False, f"File too large ({file_size} bytes)"
    if file_size < 5000:
        return False, "File too small — likely not a real screenshot"

    try:
        img = Image.open(filepath)
        w, h = img.size
        if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
            return False, f"Image too small ({w}x{h})"
        return True, "OK"
    except Exception as e:
        return False, f"Cannot open image: {e}"


# ── Main verification function ───────────────────────────────────────────────
def verify_payment_screenshot(
    filepath: str,
    expected_amount: float,
    expected_upi_id: str,
) -> dict:
    """
    Analyse a payment screenshot and return a verification result dict.

    Returns:
        {
            "confidence": int (0–100),
            "status": "verified" | "manual_review" | "failed",
            "ocr_text": str,
            "amount_detected": str | None,
            "upi_id_detected": str | None,
            "txn_id_detected": str | None,
            "success_keyword_found": bool,
            "breakdown": dict,
            "error": str | None,
        }
    """
    result = {
        "confidence": 0,
        "status": "failed",
        "ocr_text": "",
        "amount_detected": None,
        "upi_id_detected": None,
        "txn_id_detected": None,
        "success_keyword_found": False,
        "breakdown": {},
        "error": None,
    }

    # ── Step 1: Validate the image ───────────────────────────────────────
    is_valid, reason = _validate_image(filepath)
    if not is_valid:
        result["error"] = reason
        result["status"] = "failed"
        return result

    score = 0
    breakdown = {}

    # +5 for valid image
    score += 5
    breakdown["valid_image"] = 5

    # ── Step 2: If no OCR available, send to manual review ──────────────
    if not TESSERACT_AVAILABLE:
        result["confidence"] = 30
        result["status"] = "manual_review"
        result["error"] = "OCR engine not available — manual review required"
        return result

    # ── Step 3: Extract text ─────────────────────────────────────────────
    raw_text = _extract_text(filepath)
    result["ocr_text"] = raw_text

    if not raw_text.strip():
        result["error"] = "OCR returned empty text — image may be blank or corrupt"
        result["status"] = "manual_review"
        result["confidence"] = 20
        return result

    # ── Step 4: Score each check ─────────────────────────────────────────

    # Check 1: success keyword (+35)
    success_found = _find_success_keyword(raw_text)
    result["success_keyword_found"] = success_found
    kw_score = 35 if success_found else 0
    score += kw_score
    breakdown["success_keyword"] = kw_score

    # Check 2: amount match (+30)
    detected_amount_str = _extract_amount(raw_text)
    result["amount_detected"] = detected_amount_str
    amount_score = 0
    if detected_amount_str:
        try:
            detected = float(detected_amount_str)
            if abs(detected - float(expected_amount)) <= 1.0:
                amount_score = 30
            elif abs(detected - float(expected_amount)) <= 5.0:
                amount_score = 15  # partial credit
        except ValueError:
            pass
    score += amount_score
    breakdown["amount_match"] = amount_score

    # Check 3: UPI ID found (+20)
    detected_upi = _extract_upi_id(raw_text)
    result["upi_id_detected"] = detected_upi
    upi_score = 0
    if detected_upi:
        # exact match or partial match
        if expected_upi_id and expected_upi_id.lower() in raw_text.lower():
            upi_score = 20
        else:
            upi_score = 8  # found some UPI ID but not exact match
    score += upi_score
    breakdown["upi_id_match"] = upi_score

    # Check 4: transaction ID (+10)
    txn_id = _extract_transaction_id(raw_text)
    result["txn_id_detected"] = txn_id
    txn_score = 10 if txn_id else 0
    score += txn_score
    breakdown["txn_id_found"] = txn_score

    # ── Step 5: Decide status ────────────────────────────────────────────
    result["confidence"] = min(score, 100)
    result["breakdown"] = breakdown

    if score >= 75:
        result["status"] = "verified"
    elif score >= 40:
        result["status"] = "manual_review"
    else:
        result["status"] = "failed"

    logger.info(
        f"Payment verification: score={score}, status={result['status']}, "
        f"amount_detected={detected_amount_str}, upi={detected_upi}, txn={txn_id}"
    )
    return result


# ── UPI deep-link generator ──────────────────────────────────────────────────
def generate_upi_link(upi_id: str, amount: float, name: str = "Event") -> str:
    """
    Generate a UPI deep-link that opens PhonePe/Google Pay/Paytm directly.
    Format: upi://pay?pa=UPI_ID&pn=NAME&am=AMOUNT&cu=INR
    """
    from urllib.parse import quote
    safe_name = quote(name[:30])
    safe_upi = quote(upi_id)
    return f"upi://pay?pa={safe_upi}&pn={safe_name}&am={amount:.2f}&cu=INR&tn=EventFee"
