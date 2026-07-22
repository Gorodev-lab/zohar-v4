"""
core/broadcaster.py
===================
Servicio unificado de emisión y broadcasting de eventos en tiempo real para Zohar v4.
Thread-safe y seguro para ser llamado desde cualquier sub-servicio (Scrapers, LLM, Watchers)
sin acoplamiento ni importaciones circulares.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.broadcaster")


class EventBroadcaster:
    """
    Administrador central de listeners para Server-Sent Events (SSE).
    """

    def __init__(self):
        self._listeners: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return self._loop

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Asigna el event loop principal de asyncio."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Suscripción de un cliente SSE."""
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.append(q)
        logger.debug("Nuevo suscriptor registrado. Total activos: %d", len(self._listeners))
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Desuscripción de cliente SSE."""
        if q in self._listeners:
            self._listeners.remove(q)
            logger.debug("Suscriptor removido. Total activos: %d", len(self._listeners))

    def broadcast(self, event_type: str, data: Any = None, filename: Optional[str] = None):
        """
        Emite un evento a todos los clientes suscritos.
        Thread-safe: Puede invocarse desde hilos secundarios o procesos sincrónicos.
        """
        payload: Dict[str, Any] = {
            "type": event_type,
            "ts": time.time(),
        }

        if filename is not None:
            payload["file"] = filename
        if isinstance(data, dict):
            payload.update(data)
        elif data is not None and "data" not in payload:
            payload["data"] = data

        logger.debug("Emitiendo evento SSE [%s]: %s", event_type, payload)

        loop = self._get_loop()
        if not loop or not loop.is_running():
            # Si no hay event loop activo en este momento, salimos de forma segura
            return

        for q in list(self._listeners):
            try:
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except Exception as exc:
                logger.warning("Error enviando evento a cola de suscriptor: %s", exc)


# Instancia singleton global
broadcaster = EventBroadcaster()
