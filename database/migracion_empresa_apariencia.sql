CREATE TABLE empresa_apariencia (
 empresa_id BIGINT PRIMARY KEY REFERENCES empresa(id) ON DELETE CASCADE,
 version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
 datos JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(datos)='object'),
 logo BYTEA, portada BYTEA, logo_hash VARCHAR(64), portada_hash VARCHAR(64),
 actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(logo IS NULL OR octet_length(logo) <= 1048576),
 CHECK(portada IS NULL OR octet_length(portada) <= 3145728)
);
