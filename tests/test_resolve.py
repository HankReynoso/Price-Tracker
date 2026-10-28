import unittest
from unittest import mock

from app import resolve


class IsUrlTest(unittest.TestCase):
    def test_detects_links(self):
        self.assertTrue(resolve.is_url("https://www.bestbuy.com/site/sony/123.p"))
        self.assertTrue(resolve.is_url("amazon.com/dp/B09XS7JWHH"))

    def test_names_are_not_links(self):
        self.assertFalse(resolve.is_url("Sony WH-1000XM5"))
        self.assertFalse(resolve.is_url("ps5"))


class CleanTitleTest(unittest.TestCase):
    def test_strips_store_suffix(self):
        self.assertEqual(
            resolve.clean_title("Sony WH-1000XM5 Wireless Headphones - Best Buy", "www.bestbuy.com"),
            "Sony WH-1000XM5 Wireless Headphones",
        )

    def test_strips_amazon_prefix_and_marketing_tail(self):
        self.assertEqual(
            resolve.clean_title(
                "Amazon.com: Sony WH-1000XM5 Wireless Industry Leading Noise Canceling Headphones, Black : Electronics",
                "www.amazon.com",
            ),
            "Sony WH-1000XM5 Wireless Industry Leading Noise Canceling Headphones",
        )

    def test_keeps_hyphenated_model_numbers(self):
        self.assertEqual(resolve.clean_title("Apple AirPods Pro 2 | Target", "target.com"), "Apple AirPods Pro 2")


class HtmlTitleTest(unittest.TestCase):
    def test_prefers_json_ld_product_name(self):
        page = """
        <html><head><title>Shop now | Store</title>
        <meta property="og:title" content="OG name">
        <script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Nintendo Switch OLED"}]}</script>
        </head></html>"""
        self.assertEqual(resolve.page_info(page)[0], "Nintendo Switch OLED")

    def test_falls_back_to_og_title_then_title(self):
        self.assertEqual(resolve.page_info('<meta property="og:title" content="Dyson V15">')[0], "Dyson V15")
        self.assertEqual(resolve.page_info("<title>Kindle Paperwhite</title>")[0], "Kindle Paperwhite")

    def test_reads_https_preview_image(self):
        page = '<meta property="og:image" content="https://cdn.example.com/p.jpg"><title>X</title>'
        self.assertEqual(resolve.page_info(page)[1], "https://cdn.example.com/p.jpg")
        self.assertIsNone(resolve.page_info('<meta property="og:image" content="http://x/p.jpg">')[1])


class ProductNameTest(unittest.TestCase):
    def test_plain_name_is_used_as_is(self):
        self.assertEqual(resolve.product_name("  Sony   WH-1000XM5 "), ("Sony WH-1000XM5", None, None))

    def test_blocked_page_falls_back_to_url_slug(self):
        url = "https://www.amazon.com/Sony-WH-1000XM5-Canceling-Headphones-Hands-Free/dp/B09XS7JWHH"
        with mock.patch.object(resolve, "fetch_page", side_effect=OSError("503")):
            name, source, image = resolve.product_name(url)
        self.assertEqual(name, "Sony WH 1000XM5 Canceling Headphones Hands Free")
        self.assertEqual(source, url)
        self.assertIsNone(image)

    def test_unreadable_link_raises(self):
        with mock.patch.object(resolve, "fetch_page", side_effect=OSError("503")):
            with self.assertRaises(resolve.ResolveError):
                resolve.product_name("https://example.com/p/12345")


if __name__ == "__main__":
    unittest.main()
