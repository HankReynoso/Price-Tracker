import unittest

from app import demo, offers


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


if __name__ == "__main__":
    unittest.main()
