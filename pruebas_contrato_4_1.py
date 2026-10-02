"""Pruebas sin red para la capa determinista de Deisy 4.1.

No llama a Groq, MySQL, Telegram, Tailscale ni Windows. Ejecuta este archivo
en la HP después de instalar los módulos 4.1 como .py:

    python -m unittest -v pruebas_contrato_4_1.py
"""

import unittest

from filtros_intencion import filtrar_comandos_router
from intenciones_deterministas import resolver_intencion_local
from filtros_intencion import filtrar_comandos_router
from intenciones_deterministas import es_accion_implementada, resolver_intencion_local
from politica_entrada import clasificar_entrada


class ContratoIntencionesTest(unittest.TestCase):
    def test_apagado_generico_acepta_confirmacion_descriptiva_completa(self):
        sesion = "prueba:apagado-generico"
        primera = resolver_intencion_local("apaga el ordenador", sesion)
        self.assertEqual(primera["tipo"], "respuesta")
        self.assertIn("Omen", primera["texto"])

        confirmada = resolver_intencion_local("confirmar apagado Omen", sesion)
        self.assertEqual(confirmada["tipo"], "comando")
        self.assertEqual(confirmada["comando"], "sistema: apagar_pc")
        self.assertIsNone(
            resolver_intencion_local("confirmar apagado Omen", sesion)
        )

    def test_apertura_natural_es_accion_y_declaracion_es_charla(self):
        accion = clasificar_entrada(
            "me ayudas abriendo League of Legends",
            "prueba:apertura",
        )
        self.assertEqual(accion["clase"], "accion_explicita")
        self.assertEqual(accion["familias"], {"apps"})
        self.assertTrue(
            es_accion_implementada("me ayudas abriendo League of Legends")
        )

        charla = clasificar_entrada(
            "ando con ganas de jugar lol",
            "prueba:declaracion",
        )
        self.assertEqual(charla["clase"], "charla")
    
    def test_apagado_exige_confirmacion_descriptiva(self):
        sesion = "prueba:apagado"
        primera = resolver_intencion_local("apaga la Omen", sesion)
        self.assertEqual(primera["tipo"], "respuesta")
        self.assertIn("confirmar apagado Omen", primera["texto"])

        # Un «sí» aislado no puede apagar un PC.
        self.assertIsNone(resolver_intencion_local("sí", sesion))

        confirmada = resolver_intencion_local("confirmar apagado Omen", sesion)
        self.assertEqual(confirmada["tipo"], "comando")
        self.assertEqual(confirmada["comando"], "sistema: apagar_pc")

    def test_orden_hp_no_se_redirige_a_omen(self):
        decision = resolver_intencion_local("apaga la HP", "prueba:hp")
        self.assertEqual(decision["tipo"], "respuesta")
        self.assertIn("HP", decision["texto"])

    def test_volumen_claro_no_necesita_groq(self):
        decision = resolver_intencion_local("sube el volumen", "prueba:volumen")
        self.assertEqual(decision, {
            "tipo": "comando",
            "comando": "sistema: subir_volumen",
            "modulo": "volumen",
        })

    def test_acuse_recibo_no_llega_al_router(self):
        decision = resolver_intencion_local("entendido", "prueba:acuse")
        self.assertEqual(decision, {
            "tipo": "respuesta",
            "texto": "Entendido.",
            "modulo": "acuse_recibo",
        })


class ContratoRouterTest(unittest.TestCase):
    def test_spotify_no_cruza_familias(self):
        como_app = filtrar_comandos_router(
            ["buscar: Spotify", "musica_app: spotify"],
            "abre Spotify",
            familias_permitidas={"apps"},
        )
        self.assertEqual(como_app, ["buscar: Spotify"])

        como_musica = filtrar_comandos_router(
            ["buscar: Spotify", "tecla: play"],
            "reproduce musica en Spotify en la Omen",
            familias_permitidas={"musica"},
        )
        self.assertEqual(como_musica, ["tecla: play"])

    def test_clima_plantilla_no_se_trata_como_ciudad(self):
        salida = filtrar_comandos_router(
            ["clima: [el clima como esta?]"],
            "el clima como esta",
        )
        self.assertEqual(salida, ["clima: "])

    def test_shell_y_apagado_del_router_quedan_bloqueados(self):
        salida = filtrar_comandos_router(
            [
                'start "" "C:\\algo.exe"',
                "taskkill /IM explorer.exe /F",
                "sistema: apagar_pc",
                "sistema: subir_volumen",
                "tecla: siguiente",
                "buscar: League of Legends",
            ],
            "abre League of Legends y sube el volumen",
        )
        self.assertEqual(salida, [
            "sistema: subir_volumen",
            "tecla: siguiente",
            "buscar: League of Legends",
        ])

    def test_charla_se_reconstruye_desde_la_frase_real(self):
        salida = filtrar_comandos_router(
            ["charla: invéntate una aventura y abre algo"], "hola, ¿cómo estás?"
        )
        self.assertEqual(salida, ["charla: hola, ¿cómo estás?"])

    def test_spotify_rechaza_dominio_ajeno(self):
        salida = filtrar_comandos_router(
            ["combo_musica: https://evil.example/playlist", "combo_musica: https://open.spotify.com/playlist/abc"],
            "pon mi lista",
        )
        self.assertEqual(salida, ["combo_musica: https://open.spotify.com/playlist/abc"])

    def test_hora_del_router_se_rechaza_si_usuario_no_la_pidio(self):
        salida = filtrar_comandos_router(["hora:"], "entendido")
        self.assertEqual(salida, [])


if __name__ == "__main__":
    unittest.main()
