# -*- coding: utf-8 -*-
"""
Аукцион / торговая площадка. Игроки выставляют предметы за золото, другие
покупают (асинхронно — продавец может быть оффлайн). Выручка копится в
«почте» продавца и зачисляется при следующем заходе. Состояние в JSON.

Золотосток: при продаже удерживается комиссия AUCTION_FEE.
"""
import json
import time
from typing import Optional, List

AUCTION_FEE = 0.05      # 5% комиссия с продажи (сток золота)
MAX_LISTINGS = 10       # лимит активных лотов на игрока


class AuctionManager:
    def __init__(self, path: str):
        self.path = path
        self.listings = {}      # lid(str) -> {id, seller_uid, seller_name, item, price, ts}
        self.payouts = {}       # uid(str) -> накопленная бронза к выдаче
        self._next = 1
        # Режим хранения. По умолчанию (db_mode=False) — как раньше: save() пишет
        # файл сразу. Когда бот работает с БД, main() выставит db_mode=True: тогда
        # save() лишь помечает состояние «грязным», а фоновый snapshot_worker
        # периодически сбрасывает его в kv_state вместе с миром (реже, батчем).
        self.db_mode = False
        self.dirty = False
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.listings = {str(k): v for k, v in data.get("listings", {}).items()}
            self.payouts = {str(k): int(v) for k, v in data.get("payouts", {}).items()}
            self._next = data.get("next", 1)
        except (FileNotFoundError, ValueError):
            self.listings = {}; self.payouts = {}; self._next = 1

    def save(self):
        # В db-режиме не трогаем файл — только помечаем dirty (флашит snapshot_worker).
        if self.db_mode:
            self.dirty = True
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"listings": self.listings, "payouts": self.payouts,
                           "next": self._next}, f, ensure_ascii=False)
        except Exception:
            pass

    # ───────── хуки персистентности (для БД-снапшота) ─────────
    def export_state(self) -> dict:
        """JSON-safe снимок состояния аукциона (для kv_state['auction'])."""
        return {"listings": self.listings, "payouts": self.payouts,
                "next": self._next}

    def import_state(self, data: dict):
        """Загрузить состояние из снимка (kv_state) — источник вместо файла."""
        if not data:
            return
        self.listings = {str(k): v for k, v in data.get("listings", {}).items()}
        self.payouts = {str(k): int(v) for k, v in data.get("payouts", {}).items()}
        self._next = data.get("next", 1)
        self.dirty = False

    # ── выставление ──
    def my_listings(self, uid: int) -> List[dict]:
        return [l for l in self.listings.values() if l["seller_uid"] == uid]

    def create_listing(self, uid: int, name: str, item: str, price: int):
        """Создать лот. Предмет должен быть уже снят из инвентаря вызывающим."""
        if len(self.my_listings(uid)) >= MAX_LISTINGS:
            return None
        lid = str(self._next); self._next += 1
        self.listings[lid] = {"id": lid, "seller_uid": uid, "seller_name": name,
                              "item": item, "price": int(price), "ts": time.time()}
        self.save()
        return lid

    def for_sale(self, exclude_uid: int = None) -> List[dict]:
        out = [l for l in self.listings.values()
               if exclude_uid is None or l["seller_uid"] != exclude_uid]
        return sorted(out, key=lambda l: l["price"])

    def get(self, lid: str) -> Optional[dict]:
        return self.listings.get(str(lid))

    def cancel(self, lid: str, uid: int):
        """Снять свой лот. Возвращает item для возврата в инвентарь или None."""
        l = self.listings.get(str(lid))
        if not l or l["seller_uid"] != uid:
            return None
        item = l["item"]
        del self.listings[str(lid)]
        self.save()
        return item

    def buy(self, lid: str, buyer_uid: int):
        """Купить лот. Возвращает (status, listing). status: ok/missing/own/.
        Списание золота покупателя выполняет вызывающий (проверив цену)."""
        l = self.listings.get(str(lid))
        if not l:
            return "missing", None
        if l["seller_uid"] == buyer_uid:
            return "own", l
        proceeds = int(l["price"] * (1 - AUCTION_FEE))
        skey = str(l["seller_uid"])
        self.payouts[skey] = self.payouts.get(skey, 0) + proceeds
        del self.listings[str(lid)]
        self.save()
        return "ok", l

    # ── выручка продавца ──
    def claim_payout(self, uid: int) -> int:
        amt = self.payouts.pop(str(uid), 0)
        if amt:
            self.save()
        return int(amt)

    def pending_payout(self, uid: int) -> int:
        return int(self.payouts.get(str(uid), 0))
