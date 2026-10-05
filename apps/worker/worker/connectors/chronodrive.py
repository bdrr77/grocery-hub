from .base import StoreAdapter
class ChronodriveAdapter(StoreAdapter):
    name="chronodrive"
    async def health(self): return False
    async def search_by_ean(self, ean): raise NotImplementedError
    async def search(self, query): raise NotImplementedError
    async def get_cart(self): raise NotImplementedError
    async def add_item(self, product_id, quantity=1): raise NotImplementedError
    async def remove_item(self, product_id): raise NotImplementedError
    async def set_quantity(self, product_id, quantity): raise NotImplementedError
    async def clear_cart(self): raise NotImplementedError
