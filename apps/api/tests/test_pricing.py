import unittest
from app.pricing import summarize, pack_pricing, product_card


class PricingTests(unittest.TestCase):
    def test_pack_units_and_multipacks(self):
        for size, price, expected, unit in [("400 g",349,873,"kg"),("1,5 L",300,200,"L"),("6 x 33 cl",594,300,"L"),("2 × 125 g",200,800,"kg"),("12 pièces",360,30,"pièce"),("6 œufs",180,30,"pièce"),("500 ml",0,0,"L")]:
            with self.subTest(size=size):
                result = pack_pricing(size,price)
                self.assertEqual((result["unit_price_cents"], result["unit_price_unit"]),(expected,unit))

    def test_ambiguous_or_missing_pack_has_no_invented_unit_price(self):
        for size, price in [("",349),("environ 400 g",349),("400 g + 100 g offerts",349),("0 g",349),("400 g",None)]:
            self.assertIsNone(pack_pricing(size,price)["unit_price_cents"])

    def test_card_uses_store_pack_independently_of_shopping_count(self):
        self.assertEqual(pack_pricing("Le pot de 400 g",349)["unit_price_cents"],873)
        job = {"product_id":"p1","price_cents":349,"quantity":3,"candidates":[{"id":"p1","size":"400 g"}]}
        self.assertEqual(product_card(job,None)["unit_price_cents"],873)
        snapshot = {"data":{"lines":[{"id":"p1","size":"750 g"}]}}
        self.assertEqual(product_card(job,snapshot)["size"],"750 g")

    def setUp(self):
        self.job = {"ean":"12345678","product_id":"p1","quantity":2,"status":"synced"}
        self.snapshot = {"fingerprint":"f","fetched_at":1000,"data":{
            "total_cents":1999,"deposit_cents":100,"fees_cents":200,"lines":[
                {"id":"p1","quantity":2,"line_total_cents":798},
                {"id":"other","quantity":1,"line_total_cents":901},
            ]}}

    def test_list_and_whole_cart_are_separate(self):
        result = summarize(self.snapshot,[self.job],"f",True,now=1010)
        self.assertEqual(result["list_total_cents"],798)
        self.assertEqual(result["cart_total_cents"],1999)
        self.assertEqual(result["other_cart_items"],1)
        self.assertTrue(result["list_complete"])

    def test_unmatched_and_wrong_quantities_are_incomplete(self):
        for changed in [{"product_id":None},{"quantity":3},{"status":"queued"}]:
            result = summarize(self.snapshot,[{**self.job,**changed}],"f",True,now=1010)
            self.assertIsNone(result["list_total_cents"])
            self.assertEqual(result["missing_items"],1)

    def test_stale_disconnected_or_changed_account_does_not_show_current_prices(self):
        for fingerprint,connected,now in [("f",True,1500),("f",False,1010),("different",True,1010)]:
            result = summarize(self.snapshot,[self.job],fingerprint,connected,now=now)
            self.assertFalse(result["fresh"])
            self.assertIsNone(result["cart_total_cents"])
            self.assertIsNone(result["list_total_cents"])

    def test_explicit_free_line_is_valid_missing_amount_is_not(self):
        for amount in [0,None]:
            self.snapshot["data"]["lines"][0]["line_total_cents"] = amount
            result = summarize(self.snapshot,[self.job],"f",True,now=1010)
            self.assertEqual(result["list_complete"],amount==0)
            self.assertEqual(result["list_total_cents"],amount)

    def test_missing_native_total_is_unknown_and_empty_list_is_not_compared(self):
        self.snapshot["data"]["total_cents"] = None
        result = summarize(self.snapshot,[],"f",True,now=1010)
        self.assertIsNone(result["cart_total_cents"])
        self.assertFalse(result["list_complete"])
