-- Migracion: Ventas historicas para reportes comerciales multimensuales
-- Cubre ventas en Julio, Agosto, Septiembre, Octubre y Noviembre 2026
-- Empresa: StyleAR (ID 1)
-- Incluye desglose por sucursal, variedad de categorias, metodos de pago y canales de venta.

BEGIN;

CREATE OR REPLACE FUNCTION pg_temp.fn_crear_venta_hist(
    p_empresa_id BIGINT,
    p_codigo VARCHAR(40),
    p_sucursal_id BIGINT,
    p_usuario_id BIGINT,
    p_cliente_id BIGINT,
    p_tipo VARCHAR(30),
    p_metodo_pago VARCHAR(40),
    p_tipo_entrega VARCHAR(30),
    p_costo_delivery NUMERIC(12,2),
    p_fecha TIMESTAMPTZ,
    p_sku_1 TEXT,
    p_cant_1 INT,
    p_sku_2 TEXT DEFAULT NULL,
    p_cant_2 INT DEFAULT 0
) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    v_vid BIGINT;
    v_v1_id BIGINT;
    v_v1_precio NUMERIC;
    v_v2_id BIGINT;
    v_v2_precio NUMERIC;
    v_subtotal NUMERIC := 0;
    v_total NUMERIC := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM venta WHERE empresa_id = p_empresa_id AND codigo = p_codigo) THEN
        RETURN;
    END IF;

    -- Variante 1
    SELECT pv.id, COALESCE((SELECT precio FROM precio_producto WHERE producto_variante_id = pv.id AND activo = true ORDER BY id DESC LIMIT 1), 100.00)
    INTO v_v1_id, v_v1_precio
    FROM producto_variante pv
    WHERE pv.empresa_id = p_empresa_id AND pv.activo = true AND (pv.sku ILIKE p_sku_1 OR pv.id::text = p_sku_1)
    ORDER BY pv.id ASC LIMIT 1;

    IF v_v1_id IS NULL THEN
        SELECT pv.id, COALESCE((SELECT precio FROM precio_producto WHERE producto_variante_id = pv.id AND activo = true ORDER BY id DESC LIMIT 1), 100.00)
        INTO v_v1_id, v_v1_precio
        FROM producto_variante pv WHERE pv.empresa_id = p_empresa_id AND pv.activo = true ORDER BY pv.id ASC LIMIT 1;
    END IF;

    v_subtotal := p_cant_1 * v_v1_precio;

    -- Variante 2 (opcional)
    IF p_sku_2 IS NOT NULL AND p_cant_2 > 0 THEN
        SELECT pv.id, COALESCE((SELECT precio FROM precio_producto WHERE producto_variante_id = pv.id AND activo = true ORDER BY id DESC LIMIT 1), 120.00)
        INTO v_v2_id, v_v2_precio
        FROM producto_variante pv
        WHERE pv.empresa_id = p_empresa_id AND pv.activo = true AND (pv.sku ILIKE p_sku_2 OR pv.id::text = p_sku_2)
        ORDER BY pv.id DESC LIMIT 1;

        IF v_v2_id IS NULL THEN
            SELECT pv.id, COALESCE((SELECT precio FROM precio_producto WHERE producto_variante_id = pv.id AND activo = true ORDER BY id DESC LIMIT 1), 120.00)
            INTO v_v2_id, v_v2_precio
            FROM producto_variante pv WHERE pv.empresa_id = p_empresa_id AND pv.activo = true ORDER BY pv.id DESC LIMIT 1;
        END IF;

        v_subtotal := v_subtotal + (p_cant_2 * v_v2_precio);
    END IF;

    v_total := v_subtotal + p_costo_delivery;

    INSERT INTO venta (
        empresa_id, codigo, sucursal_id, usuario_id, cliente_id,
        tipo, estado, subtotal, descuento, total,
        metodo_pago, fecha_venta, activo, tipo_entrega, costo_delivery
    ) VALUES (
        p_empresa_id, p_codigo, p_sucursal_id, p_usuario_id, p_cliente_id,
        p_tipo, 'COMPLETADA', v_subtotal, 0.00, v_total,
        p_metodo_pago, p_fecha, true, p_tipo_entrega, p_costo_delivery
    ) RETURNING id INTO v_vid;

    -- Detalle 1
    INSERT INTO venta_detalle (
        empresa_id, venta_id, producto_variante_id, cantidad,
        precio_unitario, descuento, subtotal, tipo_compra
    ) VALUES (
        p_empresa_id, v_vid, v_v1_id, p_cant_1,
        v_v1_precio, 0.00, (p_cant_1 * v_v1_precio), 'UNIDAD'
    );

    INSERT INTO movimiento_inventario (
        empresa_id, sucursal_id, producto_variante_id, usuario_id,
        tipo, cantidad, stock_anterior, stock_nuevo,
        motivo, referencia_tipo, referencia_id,
        fecha_movimiento, activo
    ) VALUES (
        p_empresa_id, p_sucursal_id, v_v1_id, p_usuario_id,
        CASE WHEN p_tipo = 'WEB' THEN 'VENTA_DIGITAL' ELSE 'VENTA_PRESENCIAL' END,
        p_cant_1, 20, 20 - p_cant_1,
        'Venta ' || p_codigo, 'VENTA', v_vid,
        p_fecha, true
    );

    -- Detalle 2
    IF v_v2_id IS NOT NULL AND p_cant_2 > 0 THEN
        INSERT INTO venta_detalle (
            empresa_id, venta_id, producto_variante_id, cantidad,
            precio_unitario, descuento, subtotal, tipo_compra
        ) VALUES (
            p_empresa_id, v_vid, v_v2_id, p_cant_2,
            v_v2_precio, 0.00, (p_cant_2 * v_v2_precio), 'UNIDAD'
        );

        INSERT INTO movimiento_inventario (
            empresa_id, sucursal_id, producto_variante_id, usuario_id,
            tipo, cantidad, stock_anterior, stock_nuevo,
            motivo, referencia_tipo, referencia_id,
            fecha_movimiento, activo
        ) VALUES (
            p_empresa_id, p_sucursal_id, v_v2_id, p_usuario_id,
            CASE WHEN p_tipo = 'WEB' THEN 'VENTA_DIGITAL' ELSE 'VENTA_PRESENCIAL' END,
            p_cant_2, 18, 18 - p_cant_2,
            'Venta ' || p_codigo, 'VENTA', v_vid,
            p_fecha, true
        );
    END IF;

    -- Orden de pago (para tarjeta y QR)
    IF p_metodo_pago IN ('TARJETA', 'QR') THEN
        INSERT INTO orden_pago (
            empresa_id, venta_id, cliente_id, monto_total, moneda,
            metodo, estado, proveedor, fecha_creacion, fecha_actualizacion,
            fecha_pago, activo, concepto, modo_pago, pago_recibido
        ) VALUES (
            p_empresa_id, v_vid, p_cliente_id, v_total, 'BOB',
            p_metodo_pago, 'PAGADO',
            CASE WHEN p_metodo_pago = 'TARJETA' THEN 'STRIPE' ELSE 'QR_BCP' END,
            p_fecha, p_fecha, p_fecha + interval '1 minute',
            true, 'COMPRA', 'LEGADO', true
        );
    END IF;

    -- Delivery (para ventas web con delivery)
    IF p_tipo = 'WEB' AND p_tipo_entrega = 'DELIVERY' THEN
        INSERT INTO delivery (
            empresa_id, venta_id, sucursal_id, cliente_id,
            direccion_entrega, latitud_entrega, longitud_entrega,
            distancia_km, tiempo_estimado_min, costo_delivery,
            estado, fecha_creacion, fecha_actualizacion, fecha_entrega, activo, version
        ) VALUES (
            p_empresa_id, v_vid, p_sucursal_id, p_cliente_id,
            'Zona Central, Santa Cruz de la Sierra', -17.7833, -63.1821,
            4.2, 15, p_costo_delivery,
            'ENTREGADO', p_fecha, p_fecha, p_fecha + interval '2 hours', true, 1
        );
    END IF;
END;
$$;

DO $$
DECLARE
    v_emp BIGINT;
    v_s1 BIGINT;
    v_s2 BIGINT;
    v_caj BIGINT;
    v_ven BIGINT;
    v_cli BIGINT;
BEGIN
    SELECT id INTO v_emp FROM empresa WHERE codigo = 'stylear' OR id = 1 ORDER BY id LIMIT 1;
    IF v_emp IS NULL THEN
        RETURN;
    END IF;

    SELECT id INTO v_s1 FROM sucursal WHERE empresa_id = v_emp AND activo = true ORDER BY id ASC LIMIT 1;
    SELECT id INTO v_s2 FROM sucursal WHERE empresa_id = v_emp AND activo = true ORDER BY id DESC LIMIT 1;
    IF v_s2 IS NULL THEN
        v_s2 := v_s1;
    END IF;

    SELECT id INTO v_caj FROM usuario WHERE username IN ('cajero10', 'cajero01', 'cajero.prueba') LIMIT 1;
    IF v_caj IS NULL THEN
        SELECT id INTO v_caj FROM usuario WHERE empresa_id = v_emp LIMIT 1;
    END IF;

    SELECT id INTO v_ven FROM usuario WHERE username IN ('ventas10', 'admin10', 'ventas.prueba') LIMIT 1;
    IF v_ven IS NULL THEN
        SELECT id INTO v_ven FROM usuario WHERE empresa_id = v_emp LIMIT 1;
    END IF;

    SELECT id INTO v_cli FROM cliente WHERE empresa_id = v_emp AND activo = true ORDER BY id ASC LIMIT 1;

    -- =========================================================================
    -- JULIO 2026
    -- =========================================================================
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202607-01', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-07-22 10:15:00-04', '%POL-BAS%', 1, '%SHO-COR%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202607-02', v_s2, v_ven, v_cli, 'WEB', 'QR', 'DELIVERY', 15.00, '2026-07-26 14:30:00-04', '%HOD-BAS%', 1, '%POL-ELE%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202607-03', v_s1, v_caj, v_cli, 'PRESENCIAL', 'TARJETA', 'RECOJO_SUCURSAL', 0.00, '2026-07-30 17:20:00-04', '%JEA-REC%', 1);

    -- =========================================================================
    -- AGOSTO 2026
    -- =========================================================================
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202608-01', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-08-04 10:15:00-04', '%BAG-JEA%', 1, '%POL-BAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202608-02', v_s2, v_ven, v_cli, 'PRESENCIAL', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-08-07 15:30:00-04', '%SHO-COR%', 2);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202608-03', v_s1, v_ven, v_cli, 'WEB', 'TARJETA', 'DELIVERY', 18.00, '2026-08-11 11:20:00-04', '%HOD-BAS%', 1, '%POL-ELE%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202608-04', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-08-14 16:45:00-04', '%JEA-REC%', 1, '%POL-CAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202608-05', v_s2, v_ven, v_cli, 'WEB', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-08-18 12:10:00-04', '%POL-CAS%', 2);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202608-06', v_s2, v_caj, v_cli, 'PRESENCIAL', 'TARJETA', 'RECOJO_SUCURSAL', 0.00, '2026-08-21 17:50:00-04', '%SHO-DEP%', 1, '%Pixl%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202608-07', v_s1, v_ven, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-08-25 14:30:00-04', '%STY-SPO%', 1, '%POL-BAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202608-08', v_s1, v_ven, v_cli, 'WEB', 'TARJETA', 'DELIVERY', 15.00, '2026-08-28 18:20:00-04', '%BAG-JEA%', 1, '%HOD-BAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202608-09', v_s2, v_caj, v_cli, 'PRESENCIAL', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-08-31 11:40:00-04', '%JEA-REC%', 1);

    -- =========================================================================
    -- SEPTIEMBRE 2026
    -- =========================================================================
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202609-01', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-09-03 10:45:00-04', '%POL-BAS%', 2);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202609-02', v_s2, v_ven, v_cli, 'WEB', 'TARJETA', 'DELIVERY', 18.00, '2026-09-07 16:15:00-04', '%SHO-DEP%', 1, '%STY-SPO%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202609-03', v_s2, v_caj, v_cli, 'PRESENCIAL', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-09-11 11:30:00-04', '%POL-ELE%', 1, '%SHO-COR%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202609-04', v_s1, v_ven, v_cli, 'PRESENCIAL', 'TARJETA', 'RECOJO_SUCURSAL', 0.00, '2026-09-15 15:20:00-04', '%JEA-REC%', 1, '%POL-CAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202609-05', v_s1, v_ven, v_cli, 'WEB', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-09-19 12:40:00-04', '%Pixl%', 2);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202609-06', v_s2, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-09-23 17:10:00-04', '%HOD-BAS%', 1, '%BAG-JEA%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202609-07', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-09-26 14:00:00-04', '%STY-SPO%', 1, '%POL-ELE%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202609-08', v_s2, v_ven, v_cli, 'WEB', 'TARJETA', 'DELIVERY', 18.00, '2026-09-29 18:30:00-04', '%JEA-REC%', 1, '%SHO-COR%', 1);

    -- =========================================================================
    -- OCTUBRE 2026
    -- =========================================================================
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-01', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-10-02 11:10:00-04', '%HOD-BAS%', 1, '%POL-BAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-02', v_s2, v_caj, v_cli, 'PRESENCIAL', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-10-05 15:45:00-04', '%BAG-JEA%', 1, '%POL-CAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202610-03', v_s1, v_ven, v_cli, 'WEB', 'TARJETA', 'DELIVERY', 18.00, '2026-10-08 12:20:00-04', '%JEA-REC%', 2);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-04', v_s2, v_ven, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-10-12 16:30:00-04', '%SHO-COR%', 2, '%STY-SPO%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202610-05', v_s2, v_ven, v_cli, 'WEB', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-10-16 10:50:00-04', '%SHO-DEP%', 1, '%POL-ELE%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-06', v_s1, v_caj, v_cli, 'PRESENCIAL', 'TARJETA', 'RECOJO_SUCURSAL', 0.00, '2026-10-19 14:15:00-04', '%HOD-BAS%', 1, '%JEA-REC%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-07', v_s1, v_ven, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-10-23 18:00:00-04', '%POL-BAS%', 2, '%Pixl%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202610-08', v_s1, v_ven, v_cli, 'WEB', 'TARJETA', 'DELIVERY', 15.00, '2026-10-26 13:40:00-04', '%BAG-JEA%', 1, '%POL-CAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-09', v_s2, v_caj, v_cli, 'PRESENCIAL', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-10-29 17:25:00-04', '%SHO-DEP%', 1, '%SHO-COR%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202610-10', v_s2, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-10-31 15:10:00-04', '%STY-SPO%', 1, '%POL-ELE%', 1);

    -- =========================================================================
    -- NOVIEMBRE 2026
    -- =========================================================================
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202611-01', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-11-02 10:20:00-04', '%JEA-REC%', 1, '%POL-BAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202611-02', v_s2, v_ven, v_cli, 'WEB', 'QR', 'DELIVERY', 18.00, '2026-11-04 14:30:00-04', '%HOD-BAS%', 1, '%BAG-JEA%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202611-03', v_s2, v_caj, v_cli, 'PRESENCIAL', 'TARJETA', 'RECOJO_SUCURSAL', 0.00, '2026-11-06 16:50:00-04', '%POL-CAS%', 2);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202611-04', v_s1, v_ven, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-11-08 11:40:00-04', '%SHO-DEP%', 1, '%STY-SPO%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'WEB-HIST-202611-05', v_s1, v_ven, v_cli, 'WEB', 'TARJETA', 'RECOJO_SUCURSAL', 0.00, '2026-11-11 15:15:00-04', '%POL-ELE%', 1, '%Pixl%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202611-06', v_s2, v_caj, v_cli, 'PRESENCIAL', 'QR', 'RECOJO_SUCURSAL', 0.00, '2026-11-13 18:00:00-04', '%JEA-REC%', 1, '%HOD-BAS%', 1);
    PERFORM pg_temp.fn_crear_venta_hist(v_emp, 'VEN-HIST-202611-07', v_s1, v_caj, v_cli, 'PRESENCIAL', 'EFECTIVO', 'RECOJO_SUCURSAL', 0.00, '2026-11-15 13:30:00-04', '%SHO-COR%', 2, '%POL-BAS%', 1);

END $$;

DROP FUNCTION IF EXISTS pg_temp.fn_crear_venta_hist;

COMMIT;
