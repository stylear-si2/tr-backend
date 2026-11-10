from io import BytesIO
import pytest
from PIL import Image
from pydantic import ValidationError
from fastapi import HTTPException
from app.modules.administracion.services.apariencia_service import Apariencia, Edicion, procesar_imagen, contraste_blanco

@pytest.mark.parametrize('color',['red','#fff','#ffffff','#aaaaaa','url(x)',';background:red'])
def test_rechaza_colores_ilegibles_o_css(color):
    with pytest.raises(ValidationError):Apariencia(color_principal=color)

@pytest.mark.parametrize('campo,valor',[('sitio_web','javascript:alert(1)'),('instagram','https://evil.test'),('facebook','https://facebook.com.evil.test'),('sitio_web','https://user:password@ejemplo.com'),('sitio_web','http://ejemplo.com'),('whatsapp','hola'),('whatsapp','123')])
def test_valida_contacto(campo,valor):
    with pytest.raises(ValidationError):Apariencia(**{campo:valor})


def test_acepta_colores_y_contacto_normales():
    a=Apariencia(color_principal='#143a52',whatsapp='+59170000000',instagram='https://www.instagram.com/tienda',sitio_web='https://ejemplo.com')
    assert a.color_principal=='#143A52' and contraste_blanco(a.color_principal)>=4.5
    with pytest.raises(ValidationError):Edicion(version=0,empresa_id=99)


def test_recodifica_imagen_sin_metadatos_y_redimensiona():
    b=BytesIO();Image.new('RGB',(2000,800),'blue').save(b,format='JPEG',exif=b'Exif\x00\x00')
    resultado=procesar_imagen(b.getvalue(),'logo')
    with Image.open(BytesIO(resultado)) as img:
        assert img.format=='WEBP' and max(img.size)<=640
        assert not img.info.get('exif')

@pytest.mark.parametrize('datos',[b'<svg onload="alert(1)"></svg>',b'no es imagen',b'x'*(5*1048576+1)],ids=['svg','texto','grande'])
def test_no_acepta_archivos_arbitrarios_o_excesivos(datos):
    with pytest.raises(HTTPException):procesar_imagen(datos,'portada')
