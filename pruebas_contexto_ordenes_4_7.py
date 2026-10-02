import unittest

from contexto_ordenes import (
    olvidar_referencia_repetible,
    registrar_orden_confirmada,
    resolver_contexto_corto,
    resolver_alias_apertura,
)


class ContextoOrdenesTest(unittest.TestCase):
    def test_alias_spotify_natural_usa_inventario(self):
        comando, detalle = resolver_alias_apertura(
            "¿Me ayudas abriendo Spotify?"
        )
        self.assertEqual(comando, "buscar: Spotify")
        self.assertIn("Spotify", detalle)

    def test_reproducir_musica_es_tecla_y_no_apertura(self):
        comando, aclaracion = resolver_contexto_corto(
            "puedes reproducir musica en Spotify",
            self.sesion,
        )
        self.assertEqual(comando, "tecla: play")
        self.assertEqual(aclaracion, "")

    def setUp(self):
        # Cada prueba usa otra sesión: no depende de las demás.
        self.sesion = f"prueba-4.7-{self._testMethodName}"

    def test_mas_repite_subida_confirmada(self):
        registrar_orden_confirmada("sistema: subir_volumen", self.sesion)
        self.assertEqual(
            resolver_contexto_corto("más", self.sesion),
            ("sistema: subir_volumen", ""),
        )

    def test_mas_mantiene_direccion_de_bajada(self):
        registrar_orden_confirmada("sistema: bajar_volumen", self.sesion)
        self.assertEqual(
            resolver_contexto_corto("mas", self.sesion),
            ("sistema: bajar_volumen", ""),
        )

    def test_otra_vez_repite_siguiente_confirmada(self):
        registrar_orden_confirmada("tecla: siguiente", self.sesion)
        self.assertEqual(
            resolver_contexto_corto("Otra vez", self.sesion),
            ("tecla: siguiente", ""),
        )

    def test_otra_vez_repite_anterior_confirmada(self):
        registrar_orden_confirmada("tecla: anterior", self.sesion)
        self.assertEqual(
            resolver_contexto_corto("otra vez", self.sesion),
            ("tecla: anterior", ""),
        )

    def test_otra_vez_no_inventa_tras_pausa(self):
        registrar_orden_confirmada("tecla: pausa", self.sesion)
        comando, aclaracion = resolver_contexto_corto("otra vez", self.sesion)
        self.assertEqual(comando, "")
        self.assertIn("siguiente canción", aclaracion)

    def test_mas_sin_contexto_no_ejecuta(self):
        comando, aclaracion = resolver_contexto_corto("mas", self.sesion)
        self.assertEqual(comando, "")
        self.assertIn("volumen", aclaracion)

    def test_baja_mas_sin_contexto_no_ejecuta(self):
        comando, aclaracion = resolver_contexto_corto("baja más", self.sesion)
        self.assertEqual(comando, "")
        self.assertIn("volumen", aclaracion)

    def test_otro_chat_no_hereda_contexto(self):
        registrar_orden_confirmada("sistema: subir_volumen", self.sesion)
        comando, aclaracion = resolver_contexto_corto("mas", "otro-chat")
        self.assertEqual(comando, "")
        self.assertIn("volumen", aclaracion)

    def test_fallo_omen_borra_referencia(self):
        registrar_orden_confirmada("sistema: subir_volumen", self.sesion)
        olvidar_referencia_repetible("buscar: algo", self.sesion)
        comando, aclaracion = resolver_contexto_corto("mas", self.sesion)
        self.assertEqual(comando, "")
        self.assertIn("volumen", aclaracion)


if __name__ == "__main__":
    unittest.main()