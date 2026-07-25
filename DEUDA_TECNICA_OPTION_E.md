# Deuda Técnica — Option E (paralelo a Option D)

**Fecha:** 2026-07-25
**Estado:** FIX APLICADO (Opción A), pendiente Opción B canónica.

## Síntoma
El self-healing loop de `zohar_api` (`api/main.py::llama_self_healing_loop`)
entraba en un **bucle estéril de reinicio** del container `maritime_llama_cpp`:
cada ~3 ciclos de 30s emitía `Reiniciando contenedor...` y el container
reiniciaba una y otra vez, nunca quedando estable (healthcheck `unhealthy`).

Log característico en `zohar_api`:
```
Self-healing: ciclo N/3 poco saludable (no conecta /health: [Errno -2] Name or service not known).
Self-healing: 3 fallos consecutivos confirmados. Reiniciando contenedor...
```

## Causa raíz
`maritime_llama_cpp` fue levantado **FUERA del docker-compose**
(`docker run` directo o compose distinto) y terminó en la red por defecto
`bridge`, NO en la red `dw_default` donde vive `zohar_api`.

Evidencia (docker inspect):
- `zohar_api`      → red `dw_default`, ComposeProject=`dw`, alias `api`/`zohar_api`.
- `maritime_llama_cpp` → red `bridge`, **ComposeProject=(vacío), Service=(vacío)**.
  Creado `2026-07-25T07:32` (hoy), muy posterior a `zohar_api` (`2026-07-22`).

El health-check usa `LOCAL_LLM_URL=http://llama-cpp:8083` (servicio del compose).
`llama-cpp` solo resuelve vía DNS interno de Docker SI el container objetivo
está en la MISMA red que quien probea (`zohar_api` en `dw_default`).
Como `maritime_llama_cpp` estaba en `bridge`, el DNS `llama-cpp` no resolvía →
`Name or service not known` → el loop lo interpretaba como "server muerto" → reinicio.
El reinicio recreaba el container SIEMPRE en `bridge` → el ciclo nunca se cerraba.

NO era un mismatch de nombre en `api/main.py`: el hostname `llama-cpp` es
correcto para el compose. El bug es de **pertenencia a red / origen del container**.

## Fix aplicado — Opción A (2026-07-25)
Conectar el container existente a `dw_default` SIN recrearlo, con alias explícito:

```bash
docker network connect --alias llama-cpp dw_default maritime_llama_cpp
```

Post-fix: `maritime_llama_cpp` queda en `bridge` + `dw_default`, con
`DNSNames=["maritime_llama_cpp","llama-cpp",...]` en `dw_default`. El DNS
`llama-cpp` ahora resuelve desde `zohar_api` → el health-check ve `/health` OK
y el self-healing deja de reiniciar.

**No se tocó FASE A (OCR, PID 28748), que sigue corriendo de forma independiente.**

## Pendiente — Opción B (cuando haya ventana sin FASE A corriendo)
Recrear `maritime_llama_cpp` vía el compose canónico para que el container
viva DENTRO del proyecto `dw` (labels `com.docker.compose.service=llama-cpp`,
red `dw_default` nativa, sin depender del alias manual):

```bash
# desde /home/gorops/proyectos antigravity/zohar-v4-main/dw
docker rm -f maritime_llama_cpp          # el de bridge (fuera de compose)
docker compose up -d llama-cpp          # recrea dentro de dw_default con labels correctos
```

Esto elimina la dependencia del alias manual y deja el despliegue declarativo.
**NO se aplica mientras FASE A esté corriendo** (riesgo de interrumpir/colisionar
el reintento OCR de las 108 claves).

## Relación con Option D
- **Option D** (deuda previa): `httpx.RemoteProtocolError` en inferencia LLM
  local — vigilar por separado del health-check.
- **Option E** (este): desalineación de red/desepliegue del llama-server que
  hace que el self-healing no pueda resolver el DNS interno y reinicie en bucle.
  Ambas son deudas de infraestructura del llama-server, no de lógica de pipeline.
