import json
import unittest
from pathlib import Path

from app import demo, offers

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "google_shopping_sony_wh1000xm5.json").read_text())
BEST_BUY_CATALOG_IMAGE = (
    "https://encrypted-tbn3.gstatic.com/shopping?q=tbn:ANd9GcTmLyXlp3tLLdOA7Efd5Sgi4Wui7av2A7DKjKREHNFU3Sgq5xYK7-"
    "2997ZuqIQdP2HE-RXVNHZD-_eIyobSy4sOnmuarxcqqfZY8qtVX08CgNctHwlWtHhd"
)
MACYS_CATALOG_IMAGE = (
    "https://encrypted-tbn3.gstatic.com/shopping?q=tbn:ANd9GcTtqK8Y8iZig43lO-9iSIx1V_9gln7tBHC7fbtjAwSBEstasKsVPl99"
    "Ogx_huEd-Mj19BEsdly7oZZ727Hajpqd3h527fu3iaiOlH1FUkdbb2l1KWJn70qhUgQ"
)


def raw(title, store, price, **extra):
    return {"title": title, "source": store, "extracted_price": price, **extra}


class BuildTest(unittest.TestCase):
    def test_keeps_cheapest_new_listing_per_store_sorted(self):
        result = offers.build(
            [
                raw("Sony WH-1000XM5 Headphones", "Amazon", 329.99),
                raw("Sony WH-1000XM5 Black", "Amazon", 298.00),
                raw("Sony WH1000XM5 Wireless", "Best Buy", 279.99),
                raw("Sony WH-1000XM5", "Walmart", 310.00),
            ],
            "Sony WH-1000XM5",
        )
        self.assertEqual([o["store"] for o in result["offers"]], ["Best Buy", "Amazon", "Walmart"])
        self.assertEqual(result["lowest"]["price"], 279.99)
        self.assertEqual(result["highest"], 310.00)

    def test_hides_used_and_refurbished(self):
        result = offers.build(
            [
                raw("Sony WH-1000XM5 (Renewed)", "Amazon", 199.00),
                raw("Sony WH-1000XM5", "eBay", 210.00, second_hand_condition="used"),
                raw("Sony WH-1000XM5", "Target", 299.99),
            ],
            "Sony WH-1000XM5",
        )
        self.assertEqual([o["store"] for o in result["offers"]], ["Target"])
        self.assertEqual(result["hidden"]["used"], 2)

    def test_hides_unrelated_and_cheap_accessories(self):
        result = offers.build(
            [
                raw("Bose QuietComfort Ultra", "Amazon", 349.00),
                raw("Hard Case for Sony WH-1000XM5", "eBay", 14.99),
                raw("Sony WH-1000XM5", "Target", 299.99),
                raw("Sony WH-1000XM5", "Walmart", 289.00),
                raw("Sony WH-1000XM5", "Costco", 279.00),
            ],
            "Sony WH-1000XM5",
        )
        self.assertEqual([o["store"] for o in result["offers"]], ["Costco", "Walmart", "Target"])
        self.assertEqual(result["hidden"]["unrelated"], 2)

    def test_marketplace_sellers_group_under_the_marketplace(self):
        result = offers.build(
            [
                raw("Sony WH-1000XM5", "eBay - audio_deals", 289.00),
                raw("Sony WH-1000XM5", "eBay - bestsellers", 279.00),
                raw("Sony WH-1000XM5", "Best Buy", 299.99),
            ],
            "Sony WH-1000XM5",
        )
        self.assertEqual([(o["store"], o["price"]) for o in result["offers"]], [("eBay", 279.00), ("Best Buy", 299.99)])

    def test_skips_results_without_a_price(self):
        result = offers.build([raw("Sony WH-1000XM5", "Amazon", None)], "Sony WH-1000XM5")
        self.assertEqual(result["offers"], [])
        self.assertIsNone(result["lowest"])

    def test_demo_results_pass_filters(self):
        result = offers.build(demo.search("Sony WH-1000XM5"), "Sony WH-1000XM5")
        self.assertEqual(len(result["offers"]), 6)
        self.assertEqual(result["hidden"], {"used": 1, "unrelated": 1})


class ImageTest(unittest.TestCase):
    query = "Sony WH-1000XM5"

    def test_real_results_pick_a_stores_catalog_picture(self):
        self.assertEqual(offers.build(FIXTURE["shopping_results"], self.query)["image"], BEST_BUY_CATALOG_IMAGE)

    def test_catalog_picture_beats_an_earlier_store_listing_picture(self):
        # Without Best Buy's clustered result, Lowe's unclustered listing comes first but Macy's clustered one wins.
        results = [r for r in FIXTURE["shopping_results"] if r["thumbnail"] != BEST_BUY_CATALOG_IMAGE]
        self.assertEqual(offers.build(results, self.query)["image"], MACYS_CATALOG_IMAGE)

    def test_real_results_from_sellers_only_have_no_picture(self):
        results = [r for r in FIXTURE["shopping_results"] if " - " in r["source"] or r.get("second_hand_condition")]
        result = offers.build(results, self.query)
        self.assertTrue(result["offers"])
        self.assertIsNone(result["image"])

    def test_seller_marketplace_and_used_photos_are_never_used(self):
        result = offers.build(
            [
                raw("Sony WH-1000XM5", "Walmart - Focus Camera", 299.00, thumbnail="https://img/seller.jpg", multiple_sources=True),
                raw("Sony WH-1000XM5", "eBay", 279.00, thumbnail="https://img/ebay.jpg", multiple_sources=True),
                raw("Sony WH-1000XM5", "Mercari", 269.00, thumbnail="https://img/mercari.jpg"),
                raw("Sony WH-1000XM5", "Target", 199.00, thumbnail="https://img/used.jpg", second_hand_condition="used"),
                raw("Sony WH-1000XM5", "Best Buy", 299.99),
            ],
            self.query,
        )
        self.assertEqual(len(result["offers"]), 4)
        self.assertIsNone(result["image"])

    def test_bare_marketplace_listing_loses_to_a_store(self):
        result = offers.build(
            [
                raw("Sony WH-1000XM5", "eBay", 279.00, thumbnail="https://img/ebay.jpg", multiple_sources=True),
                raw("Sony WH-1000XM5", "Target", 299.99, thumbnail="https://img/target.jpg"),
            ],
            self.query,
        )
        self.assertEqual(result["image"], "https://img/target.jpg")

    def test_uses_a_stores_pricier_listing_when_its_cheapest_has_no_picture(self):
        result = offers.build(
            [
                raw("Sony WH-1000XM5", "Best Buy", 279.99),
                raw("Sony WH-1000XM5 Black", "Best Buy", 299.99, thumbnail="https://img/bestbuy.jpg"),
                raw("Sony WH-1000XM5", "eBay - deals", 259.00, thumbnail="https://img/seller.jpg"),
            ],
            self.query,
        )
        self.assertEqual([(o["store"], o["price"]) for o in result["offers"]], [("eBay", 259.00), ("Best Buy", 279.99)])
        self.assertEqual(result["image"], "https://img/bestbuy.jpg")

    def test_unrelated_and_accessory_pictures_are_never_used(self):
        result = offers.build(
            [
                raw("Bose QuietComfort Ultra", "Amazon", 349.00, thumbnail="https://img/bose.jpg", multiple_sources=True),
                raw("Hard Case for Sony WH-1000XM5", "Amazon", 14.99, thumbnail="https://img/case.jpg", multiple_sources=True),
                raw("Sony WH-1000XM5", "Walmart", 289.00),
                raw("Sony WH-1000XM5", "Target", 299.99, thumbnail="https://img/target.jpg"),
                raw("Sony WH-1000XM5", "Costco", 279.00),
            ],
            self.query,
        )
        self.assertEqual(result["image"], "https://img/target.jpg")


if __name__ == "__main__":
    unittest.main()
