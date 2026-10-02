import unittest
from unittest.mock import Mock, patch

import politica_entrada
from politica_entrada import registrar_oferta_basica, resolver_oferta_basica


class OfertasBasicasTest(unittest.TestCase):
    def setUp(self):
        self.sesion = f"prueba-oferta-4.8-{self._testMethodName}"

    def test_registra_y_acepta_spotify_una_vez(self):
        texto = registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        self.assertIn("Spotify", texto)

        plan = resolver_oferta_basica(self.sesion, "Sí, por favor")
        self.assertEqual(plan["estado"], "aceptada")
        self.assertEqual(plan["acciones"], ("musica_app: spotify",))
        self.assertEqual(plan["familias"], {"musica"})

        # El segundo sí no puede reutilizar el mismo permiso.
        self.assertIsNone(resolver_oferta_basica(self.sesion, "sí"))

    def test_otro_chat_no_puede_aceptar(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        self.assertIsNone(resolver_oferta_basica("otro-chat", "sí"))

    def test_no_cancela_la_oferta(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        resultado = resolver_oferta_basica(self.sesion, "No, gracias")
        self.assertEqual(resultado["estado"], "cancelada")
        self.assertIsNone(resolver_oferta_basica(self.sesion, "sí"))

    def test_frase_nueva_cancela_permiso_antiguo(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        self.assertIsNone(resolver_oferta_basica(self.sesion, "cuéntame algo"))
        self.assertIsNone(resolver_oferta_basica(self.sesion, "sí"))

    def test_cualquiera_no_inventa_playlist(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        resultado = resolver_oferta_basica(self.sesion, "cualquiera")
        self.assertEqual(resultado["estado"], "aclarar")
        self.assertIn("playlist", resultado["respuesta"])

    def test_codigo_ajeno_no_crea_oferta(self):
        self.assertEqual(registrar_oferta_basica(self.sesion, "sistema: apagar_pc"), "")

    def test_lote_futuro_no_puede_entrar_en_esta_fase(self):
        plantilla_lote = {
            "acciones": ("musica_app: spotify", "sistema: subir_volumen"),
            "familias": frozenset({"musica"}),
            "texto": "No debería mostrarse.",
        }
        with patch.dict(
            politica_entrada._OFERTAS_BASICAS,
            {"lote_prohibido": plantilla_lote},
            clear=False,
        ):
            self.assertEqual(registrar_oferta_basica(self.sesion, "lote_prohibido"), "")

    def test_oferta_caducada_no_se_puede_aceptar(self):
        # No esperamos 90 segundos reales: simulamos el reloj local.
        with patch("politica_entrada.time.monotonic", return_value=100.0):
            registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        with patch("politica_entrada.time.monotonic", return_value=191.0):
            self.assertIsNone(resolver_oferta_basica(self.sesion, "sí"))


if __name__ == "__main__":
    unittest.main()