"""
SENTIERO — Πλήρες pipeline για GitHub Actions  (v2 — fixed)
=============================================================
Τραβάει τα πιο πρόσφατα προϊόντα από το New Collection (WooCommerce
REST API), φτιάχνει ένα creative ανά προϊόν, και τα ενώνει σε ένα
τελικό MP4. Σχεδιασμένο να τρέχει αυτόματα μέσα σε GitHub Actions —
δεν χρειάζεται δικό σας server.

Τα credentials ΔΕΝ μπαίνουν εδώ μέσα ποτέ. Διαβάζονται από
environment variables, που τα δίνει το GitHub Actions workflow από
τα Repository Secrets (βλ. README.md).

Διορθώσεις v2:
  - Bug #1 : Category ID lookup από slug (το WC API δέχεται μόνο ID)
  - Bug #2 : Pagination loop — λειτουργεί και για >100 προϊόντα
  - Bug #3 : Pixel-based word wrap με font.getlength() αντί χαρακτήρων
  - Fix    : Γραμματοσειρά → Noto Sans Bold (πλήρη ελληνικά & λατινικά)
  - Fix    : try/except στο download εικόνων (αποτυχία = παράλειψη)
  - Bonus  : Εμφάνιση sale τιμής αν υπάρχει + ημερομηνία ανανέωσης
"""

import os
import shutil
import subprocess
from datetime import date
import requests
from PIL import Image, ImageDraw, ImageFont

# ================= CONFIG =================
# Τα credentials ΔΕΝ γράφονται ποτέ εδώ — έρχονται από GitHub Secrets.
CONSUMER_KEY    = os.environ["WC_CONSUMER_KEY"]
CONSUMER_SECRET = os.environ["WC_CONSUMER_SECRET"]
WC_BASE_URL     = os.environ.get("WC_BASE_URL", "https://sentieroclothing.gr").rstrip("/")

# FIX: CATEGORY_SLUG τώρα διαβάζεται και από env var για ευελιξία
CATEGORY_SLUG = os.environ.get("WC_CATEGORY_SLUG", "new-collection")
NUM_PRODUCTS  = int(os.environ.get("NUM_PRODUCTS", "50"))

SECONDS_PER_PRODUCT = 4
FADE_SECONDS        = 0.3
FPS                 = 30

REPO_ROOT     = os.path.dirname(os.path.abspath(__file__))
WORKDIR       = os.path.join(REPO_ROOT, "pipeline_run")
CREATIVES_DIR = os.path.join(WORKDIR, "creatives")
CLIPS_DIR     = os.path.join(WORKDIR, "clips")
FINAL_VIDEO   = os.path.join(REPO_ROOT, "sentiero_new_collection.mp4")

CANVAS_W, CANVAS_H = 1440, 2560
BRAND_COLOR  = (17, 17, 17)
ACCENT_COLOR = (255, 255, 255)
BADGE_COLOR  = (200, 30, 30)

# FIX: Noto Sans Bold — πλήρης υποστήριξη ελληνικών χαρακτήρων.
# Εγκαθίσταται με: sudo apt-get install -y fonts-noto  (βλ. generate_video.yml)
# DejaVu χρησιμοποιείται μόνο ως last-resort fallback.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans_Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONT_BOLD = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)
if FONT_BOLD is None:
    raise FileNotFoundError(
        "Δεν βρέθηκε γραμματοσειρά. Πρόσθεσε 'fonts-noto' στο apt-get του workflow."
    )

LOGO_PATH = os.path.join(REPO_ROOT, "assets", "sentiero-logo-wh.png")
AUTH = (CONSUMER_KEY, CONSUMER_SECRET)


# ================= 1) ΑΝΤΛΗΣΗ ΠΡΟΪΟΝΤΩΝ =================

def get_category_id(slug):
    """
    FIX #1: Το WooCommerce REST API v3 δέχεται μόνο αριθμητικό category ID,
    όχι slug. Κάνουμε lookup πρώτα μέσω /product_categories?slug=...
    """
    url  = f"{WC_BASE_URL}/wp-json/wc/v3/products/categories"
    resp = requests.get(url, params={"slug": slug}, auth=AUTH, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise SystemExit(
            f"Κατηγορία '{slug}' δεν βρέθηκε. "
            "Έλεγξε ότι το slug είναι σωστό και τα API credentials έχουν δικαιώματα ανάγνωσης."
        )
    cat_id = data[0]["id"]
    print(f"     Κατηγορία '{slug}' → ID: {cat_id}")
    return cat_id


def fetch_products(category_id):
    """
    FIX #2: Pagination loop — ανακτά έως NUM_PRODUCTS προϊόντα με σελιδοποίηση,
    χειρίζεται το όριο των 100 ανά σελίδα του WooCommerce.
    """
    url      = f"{WC_BASE_URL}/wp-json/wc/v3/products"
    products = []
    page     = 1

    while len(products) < NUM_PRODUCTS:
        batch_size = min(100, NUM_PRODUCTS - len(products))
        params = {
            "category": category_id,
            "orderby":  "date",
            "order":    "desc",
            "per_page": batch_size,
            "page":     page,
            "status":   "publish",
        }
        resp = requests.get(url, params=params, auth=AUTH, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for item in batch:
            image_url = item["images"][0]["src"] if item.get("images") else None
            products.append({
                "name":       item["name"],
                "price":      item.get("regular_price", item.get("price", "0")),
                "sale_price": item.get("sale_price", ""),
                "image_url":  image_url,
            })
        page += 1

    return products[:NUM_PRODUCTS]


def download_image(url, dest_path):
    """FIX: try/except — αποτυχία CDN = παράλειψη προϊόντος, όχι crash."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"     ⚠ Αδύνατη λήψη: {url}\n       Αιτία: {e}")
        return False


# ================= 2) ΔΗΜΙΟΥΡΓΙΑ CREATIVE =================
def cover_fit(img, w, h):
    src_ratio = img.width / img.height
    target_ratio = w / h
    if src_ratio > target_ratio:
        new_h, new_w = h, int(h * src_ratio)
    else:
        new_w, new_h = w, int(w / src_ratio)
    img = img.resize((new_w, new_h))
    left, top = (new_w - w) // 2, (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def wrap_text(text, font, max_width):
    """
    FIX #3: Pixel-based word wrap.
    Αντί να μετράμε χαρακτήρες, μετράμε πραγματικό πλάτος σε pixels
    με font.getlength() — σωστό για αναλογικές γραμματοσειρές.
    """
    words, lines, current = text.split(), [], ""
    for word in words:
        test = (current + " " + word).strip()
        if font.getlength(test) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_creative(product, image_path, index):
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), ACCENT_COLOR)
    draw   = ImageDraw.Draw(canvas)

    # ── Top bar με λογότυπο ──
    top_bar_h = 180
    draw.rectangle([0, 0, CANVAS_W, top_bar_h], fill=BRAND_COLOR)
    logo      = Image.open(LOGO_PATH).convert("RGBA")
    logo_max_h = int(top_bar_h * 0.55)
    scale     = logo_max_h / logo.height
    logo      = logo.resize((int(logo.width * scale), logo_max_h), Image.LANCZOS)
    canvas.paste(logo, ((CANVAS_W - logo.width) // 2, (top_bar_h - logo.height) // 2), logo)

    # ── Badge "ΝΕΟ" ──
    badge_font = ImageFont.truetype(FONT_BOLD, 40)
    bx, by, bw, bh = 60, top_bar_h + 40, 200, 80
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=12, fill=BADGE_COLOR)
    draw.text((bx + bw / 2, by + bh / 2), "NEO", font=badge_font, fill=ACCENT_COLOR, anchor="mm")

    # ── Φωτογραφία προϊόντος ──
    img_area_top, img_area_h, img_margin = top_bar_h + 160, 1700, 60
    product_img = cover_fit(
        Image.open(image_path).convert("RGB"),
        CANVAS_W - img_margin * 2,
        img_area_h,
    )
    canvas.paste(product_img, (img_margin, img_area_top))

    # ── Τίτλος με pixel-based wrap (FIX #3) ──
    title_font = ImageFont.truetype(FONT_BOLD, 58)
    max_text_w = CANVAS_W - 120   # 60px margin από κάθε πλευρά
    title_y    = img_area_top + img_area_h + 55
    lines      = wrap_text(product["name"], title_font, max_text_w)
    for li, line in enumerate(lines[:3]):   # max 3 γραμμές
        draw.text((CANVAS_W / 2, title_y + li * 75), line,
                  font=title_font, fill=BRAND_COLOR, anchor="mm")

    # ── Τιμή (με sale αν υπάρχει) ──
    price_font = ImageFont.truetype(FONT_BOLD, 80)
    price_y    = CANVAS_H - 145
    sale       = product.get("sale_price", "")

    if sale and sale != product["price"]:
        # Κανονική τιμή με strikethrough
        small_font = ImageFont.truetype(FONT_BOLD, 48)
        draw.text((CANVAS_W / 2, price_y - 55),
                  f"{product['price']} EUR", font=small_font, fill=(160, 160, 160), anchor="mm")
        bbox = draw.textbbox((CANVAS_W / 2, price_y - 55),
                             f"{product['price']} EUR", font=small_font, anchor="mm")
        mid  = (bbox[1] + bbox[3]) // 2
        draw.line([(bbox[0], mid), (bbox[2], mid)], fill=(160, 160, 160), width=3)
        # Sale τιμή σε κόκκινο
        draw.text((CANVAS_W / 2, price_y + 15),
                  f"{sale} EUR", font=price_font, fill=BADGE_COLOR, anchor="mm")
    else:
        draw.text((CANVAS_W / 2, price_y), f"{product['price']} EUR",
                  font=price_font, fill=BRAND_COLOR, anchor="mm")

    # ── Footer: ημερομηνία ανανέωσης ──
    footer_font = ImageFont.truetype(FONT_BOLD, 28)
    today_str   = date.today().strftime("%d %b %Y")
    draw.text((CANVAS_W - 40, CANVAS_H - 28), today_str,
              font=footer_font, fill=(200, 200, 200), anchor="rs")

    out_path = os.path.join(CREATIVES_DIR, f"creative_{index:03d}.png")
    canvas.save(out_path)
    return out_path




# ================= 3) ΣΥΝΑΡΜΟΛΟΓΗΣΗ VIDEO =================
def make_clip_from_image(image_path, index):
    clip_path = os.path.join(CLIPS_DIR, f"clip_{index:03d}.mp4")
    fade_out_start = SECONDS_PER_PRODUCT - FADE_SECONDS
    vf = f"fade=t=in:st=0:d={FADE_SECONDS},fade=t=out:st={fade_out_start}:d={FADE_SECONDS},format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-t", str(SECONDS_PER_PRODUCT),
           "-vf", vf, "-r", str(FPS), "-pix_fmt", "yuv420p", clip_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return clip_path


def concat_clips(clip_paths, output_path):
    list_file = os.path.join(WORKDIR, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    if os.path.exists(WORKDIR):
        shutil.rmtree(WORKDIR)
    os.makedirs(CREATIVES_DIR)
    os.makedirs(CLIPS_DIR)

    print("1/3 — Εύρεση κατηγορίας & άντληση προϊόντων από WooCommerce...")
    category_id = get_category_id(CATEGORY_SLUG)
    products    = fetch_products(category_id)
    print(f"     Βρέθηκαν {len(products)} προϊόντα.")
    if not products:
        raise SystemExit("Δεν βρέθηκαν προϊόντα — έλεγξε το category slug ή τα credentials.")

    print("2/3 — Δημιουργία creatives...")
    clip_paths = []
    skipped    = 0
    for i, product in enumerate(products, start=1):
        if not product.get("image_url"):
            print(f"     [{i}] ΠΑΡΑΛΕΙΨΗ — {product['name']} (χωρίς φωτογραφία)")
            skipped += 1
            continue
        image_path = os.path.join(CREATIVES_DIR, f"src_{i:03d}.jpg")
        if not download_image(product["image_url"], image_path):
            skipped += 1
            continue
        creative_path = build_creative(product, image_path, i)
        clip_paths.append(make_clip_from_image(creative_path, i))
        print(f"     [{i}/{len(products)}] ✅ {product['name']}")

    if not clip_paths:
        raise SystemExit("Κανένα clip δεν δημιουργήθηκε — ελέγξτε τις εικόνες των προϊόντων.")

    print(f"\n     Δημιουργήθηκαν {len(clip_paths)} clips (παραλείφθηκαν: {skipped}).")
    print("3/3 — Συναρμολόγηση τελικού MP4...")
    concat_clips(clip_paths, FINAL_VIDEO)
    print(f"\n✅ Έτοιμο: {FINAL_VIDEO}")


if __name__ == "__main__":
    main()
