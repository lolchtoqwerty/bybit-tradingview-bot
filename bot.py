@@ def get_symbol_info(symbol: str):
-    filters = info.get("lotSizeFilter", {})
-    min_qty = float(filters.get("minOrderQty", 0))
-    step    = float(filters.get("qtyStep",    1))
-    logger.info(f"Symbol {symbol}: minQty={min_qty}, step={step}")
-    return min_qty, step
+    filters       = info.get("lotSizeFilter", {})
+    min_qty       = float(filters.get("minOrderQty",    0))
+    step          = float(filters.get("qtyStep",        1))
+    min_notional  = float(filters.get("minNotionalValue", 0))
+    logger.info(f"Symbol {symbol}: minQty={min_qty}, step={step}, minNotional={min_notional}")
+    return min_qty, step, min_notional

+def get_ticker_price(symbol: str):
+    """Получаем текущую рыночную цену для расчёта стоимости ордера."""
+    resp = http_get("v5/market/tickers", {"category":"linear", "symbol": symbol})
+    data = resp.json()
+    price = float(data["result"]["list"][0]["lastPrice"])
+    return price

@@ def set_leverage(symbol: str, long_leverage: int = 3, short_leverage: int = 1):
-    path = "v5/position/leverage/save"
+    path = "v5/position/set-leverage"  # правильный путь по документации
    body = {
        "category": "linear",
        "symbol": symbol,
        "buyLeverage": long_leverage,
        "sellLeverage": short_leverage
    }

@@ def place_order(symbol: str, side: str, qty: float, reduce_only: bool = False):
-    min_qty, step = get_symbol_info(symbol)
-    adjusted = max(min_qty, step * round(qty / step))
+    # Получаем ограничения
+    min_qty, step, min_notional = get_symbol_info(symbol)
+    price = get_ticker_price(symbol)
+
+    # Округляем запрошенное количество по шагу вверх
+    from math import ceil
+    qty_step    = step * ceil(qty / step)
+    # Учитываем минимум по стоимости: qty >= min_notional / price
+    qty_notional = step * ceil(min_notional / (price * step))
+
+    # Выбираем максимальное из трёх: минимум по лоту, по шагу, по нотионалу
+    adjusted = max(min_qty, qty_step, qty_notional)
     logger.debug(f"Adjusted qty from {qty} to {adjusted}")
     body = {
         "category": "linear",
