import unittest
from app.pricing import summarize


class PricingTests(unittest.TestCase):
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
