"""
Script para inicializar o migrar la base de datos (local o en la nube).
Ejecuta los scripts SQL en el orden correcto usando la variable DATABASE_URL.
Uso: python init_db.py
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.database.connection import get_connection

SQL_FILES = [
    "database/script_inicial.sql",
    "database/migracion_cu10_cu11.sql",
    "database/migracion_ciclo2_bloque1_catalogo_disponibilidad.sql",
    "database/migracion_ciclo2_bloque2_carrito_reservas.sql",
    "database/migracion_ciclo2_bloque3_inventario_ventas.sql",
    "database/migracion_reservas_cita_anticipo.sql",
    "database/migracion_cu22_recomendaciones.sql",
    "database/migracion_pasarela_pago_stripe.sql",
    "database/migracion_reservas_pagos_cliente.sql",
    "database/migracion_venta_digital_movimiento.sql",
    "database/migracion_roles_permisos_base.sql",
    "database/migracion_historial_pagos.sql",
    "database/migracion_delivery.sql",
    "database/migracion_password_reset.sql",
    "database/migracion_cu24_admin_ar_medidas.sql",
    "database/migracion_cu24_talla_tipo_prenda.sql",
    "database/migracion_cu24_vestidor.sql",
    "database/migracion_reportes_programados.sql",
    "database/migracion_panel_proveedor.sql",
    "database/migracion_devoluciones.sql",
]

# Las migraciones nuevas se aplican una sola vez y deben fallar en forma
# bloqueante. Los SQL legados se conservan para bases que aun no tienen esta
# version; volver a ejecutarlos despues podria restaurar unicidades globales.
VERSIONED_SQL_FILES = [
    # Mantener el orden de versiones: mayorista se agrega al final.
    ("2026_10_23_multitenant_01", "database/migracion_multitenant_01.sql"),
    ("2026_10_23_multitenant_02", "database/migracion_multitenant_02.sql"),
    ("2026_10_23_cu02_cu03_01", "database/migracion_cu02_cu03_01.sql"),
    ("2026_10_23_cu04_cu05_01", "database/migracion_cu04_cu05_01.sql"),
    ("2026_10_24_cu06_01", "database/migracion_cu06_01.sql"),
    ("2026_10_24_cu07_cu08_01", "database/migracion_cu07_cu08_01.sql"),
    ("2026_10_25_cu10_01", "database/migracion_cu10_01.sql"),
    ("2026_10_27_cu09_unicode_01", "database/migracion_cu09_unicode_01.sql"),
    ("2026_10_27_cu11_cu15_01", "database/migracion_cu11_cu15_01.sql"),
    ("2026_10_28_registro_empresa_01", "database/migracion_registro_empresa_01.sql"),
    ("2026_10_30_cu16_bloque1", "database/migracion_cu16_bloque1.sql"),
    ("2026_10_30_cu17_mayorista", "database/migracion_cu17_mayorista.sql"),
    ("2026_10_31_cu18_delivery", "database/migracion_cu18_delivery.sql"),
    ("2026_10_31_posventa_politicas", "database/migracion_posventa_politicas.sql"),
    ("2026_10_31_cu19_cambios", "database/migracion_cu19_cambios.sql"),
    ("2026_11_05_cu25_respaldos_r2", "database/migracion_cu25_respaldos_r2.sql"),
    ("2026_11_05_cu26_planes", "database/migracion_cu26_planes.sql"),
    ("2026_11_06_cu22_cu27_mejoras", "database/migracion_cu22_cu27_mejoras.sql"),
    ("2026_11_07_cu26_stripe", "database/migracion_cu26_stripe.sql"),
    ("2026_11_08_cu21_offline", "database/migracion_cu21_operacion_offline.sql"),
    ("2026_11_08_cu19_prenda_adicional", "database/migracion_cu19_prenda_adicional.sql"),
    ("2026_11_08_posventa_no_vendible", "database/migracion_posventa_no_vendible.sql"),
    ("2026_11_08_cu26_planes_usd", "database/migracion_cu26_planes_usd.sql"),
    ("2026_11_08_cu26_contratacion_planes", "database/migracion_cu26_contratacion_planes.sql"),
    ("2026_11_09_comprobantes_pago", "database/migracion_comprobantes_pago.sql"),
    ("2026_11_10_empresa_apariencia", "database/migracion_empresa_apariencia.sql"),
]


def _crear_ledger(cur, conn):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(100) PRIMARY KEY,
            archivo VARCHAR(255) NOT NULL,
            aplicada_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def _version_aplicada(cur, version):
    cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s;", (version,))
    return cur.fetchone() is not None


def _ejecutar_versionada(cur, conn, version, rel_path):
    full_path = os.path.join(os.path.dirname(__file__), rel_path)
    if not os.path.exists(full_path):
        raise RuntimeError(f"Falta migracion obligatoria: {rel_path}")

    try:
        # Serializa dos despliegues concurrentes y registra version + SQL en la
        # misma transaccion; nunca marca la version si el SQL falla.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s));", (version,))
        if _version_aplicada(cur, version):
            print(f"Ya aplicada: {version}")
            conn.commit()
            return

        print(f"Ejecutando migracion critica {version}: {rel_path}...")
        with open(full_path, "r", encoding="utf-8") as sql_file:
            cur.execute(sql_file.read())
        cur.execute(
            "INSERT INTO schema_migrations (version, archivo) VALUES (%s, %s);",
            (version, rel_path),
        )
        conn.commit()
        print(f"OK: {version}")
    except Exception as ex:
        conn.rollback()
        raise RuntimeError(f"Fallo la migracion critica {version}; despliegue detenido.") from ex


def ejecutar_scripts():
    print("Iniciando configuracion de base de datos...")
    conn = get_connection()
    try:
        cur = conn.cursor()
        _crear_ledger(cur, conn)
        version_base = VERSIONED_SQL_FILES[0][0]
        saltar_legados = _version_aplicada(cur, version_base)
        conn.rollback()

        for rel_path in ([] if saltar_legados else SQL_FILES):
            full_path = os.path.join(os.path.dirname(__file__), rel_path)
            if not os.path.exists(full_path):
                print(f"Advertencia: no se encontro {rel_path}, saltando...")
                continue
            print(f"Ejecutando {rel_path}...")
            with open(full_path, "r", encoding="utf-8") as f:
                sql = f.read()
            try:
                cur.execute(sql)
                conn.commit()
                print(f"OK: {rel_path}")
            except Exception as ex:
                conn.rollback()
                print(f"Nota en {rel_path}: {ex}")
                if rel_path == "database/migracion_devoluciones.sql":
                    raise RuntimeError("No se pudo aplicar la migración obligatoria de devoluciones.") from ex

        if not saltar_legados:
            try:
                cur.execute("ALTER TABLE categoria ADD COLUMN IF NOT EXISTS categoria_padre_id BIGINT NULL;")
                conn.commit()
                print("OK: Columna categoria_padre_id verificada.")
            except Exception:
                conn.rollback()

        for version, rel_path in VERSIONED_SQL_FILES:
            _ejecutar_versionada(cur, conn, version, rel_path)

        print("Base de datos configurada correctamente.")
    finally:
        conn.close()


if __name__ == "__main__":
    ejecutar_scripts()
