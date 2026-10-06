# Demo en Oracle Cloud

Una VM ARM gratuita de Oracle con OSRM, la API, la web y Caddy (HTTPS); el LLM, por la API
gratuita de Groq (la VM no tiene GPU). Funciona aunque tu equipo esté apagado.

```
navegador ──https──▶ Caddy ──▶ web (Next.js) ──/api──▶ API (FastAPI) ──▶ OSRM, DuckDB, LanceDB
                                                             └──────────▶ Groq (LLM)
```

## 1. Cuentas

- **Groq**: crea una clave en <https://console.groq.com/keys> (gratis, sin tarjeta).
- **Oracle Cloud**: cuenta Free Tier en <https://www.oracle.com/cloud/free/> (pide tarjeta para
  verificar; los recursos Always Free no se cobran). Elige como región de inicio una con
  capacidad ARM (Madrid o Fráncfort).

## 2. Máquina virtual

En la consola de Oracle: *Compute → Instances → Create instance*.

- Imagen: **Canonical Ubuntu 24.04** (aarch64). Forma: **VM.Standard.A1.Flex**, 2 OCPU y 12 GB
  (el máximo gratuito desde junio de 2026).
- Sube tu clave SSH pública (o descarga la que genera Oracle) y apunta la **IP pública**.
- Abre los puertos web: en la *VCN → Security List* de la subred, reglas de entrada TCP 80 y 443
  desde `0.0.0.0/0`. La imagen de Ubuntu de Oracle además los cierra con iptables:

```bash
ssh ubuntu@IP
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker ubuntu && exit
```

## 3. Código y datos

Los datos no están en git: se copian desde tu equipo (desde la carpeta del repo, en PowerShell).

```bash
ssh ubuntu@IP "git clone https://github.com/inigo99/navarra-trip-agent.git"
scp backend/data/navarra.duckdb ubuntu@IP:navarra-trip-agent/backend/data/
scp -r backend/data/lancedb ubuntu@IP:navarra-trip-agent/backend/data/
ssh ubuntu@IP "curl -L -o navarra-trip-agent/infra/osrm/navarra-latest.osm.pbf https://download.geofabrik.de/europe/spain/navarra-latest.osm.pbf"
```

## 4. Arrancar

```bash
ssh ubuntu@IP
cd navarra-trip-agent/infra/oracle
cp .env.example .env && nano .env   # GROQ_API_KEY y DOMINIO = IP con guiones + .sslip.io
docker compose up -d --build        # la 1.ª vez: grafos de OSRM e imágenes, ~15 min
```

La demo queda en `https://<IP-con-guiones>.sslip.io` (sslip.io resuelve ese nombre a la IP y
Caddy saca el certificado de Let's Encrypt). La primera petición tarda más: descarga el modelo
de embeddings (e5-base, ~1 GB) a `backend/data/hf`.

- Logs: `docker compose logs -f api`. Actualizar: `git pull && docker compose up -d --build`.
- Límite: `NAVARRA_LIMITE_HORA` planes por IP y hora (la clave gratuita de Groq tiene tope
  diario). Otro modelo: `NAVARRA_LLM` (los de tu clave: `GET https://api.groq.com/openai/v1/models`).
- Oracle puede recuperar las instancias Always Free que considere inactivas.
