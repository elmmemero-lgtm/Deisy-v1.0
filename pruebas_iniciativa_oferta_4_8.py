import os
import unittest
from unittest.mock import patch

from iniciativa import decidir_iniciativa


class IniciativaOfertaTest(unittest.TestCase):
    def test_candidata_no_contiene_comando(self):
        with patch.dict(
            os.environ,
            {
                "DEISY_INICIATIVA_ACTIVA": "true",
                "DEISY_OFERTAS_BASICAS_ACTIVAS": "true",
            },
            clear=False,
        ):
            decision = decidir_iniciativa(
                "prueba-oferta-casa",
                "Nada en especial, ¿y tú?",
                {
                    "tema": "conversacion_general",
                    "objetivo": "conversar_con_claridad",
                    "modo": "conversacion",
                },
                {"modo": "Casa"},
            )

        self.assertTrue(decision.intervenir)
        self.assertEqual(decision.tipo, "oferta_local")
        self.assertEqual(decision.oferta_local, "abrir_spotify_omen")
        self.assertNotIn(":", decision.oferta_local)

    def test_sueno_no_propone_oferta(self):
        with patch.dict(
            os.environ,
            {
                "DEISY_INICIATIVA_ACTIVA": "true",
                "DEISY_OFERTAS_BASICAS_ACTIVAS": "true",
            },
            clear=False,
        ):
            decision = decidir_iniciativa(
                "prueba-oferta-sueno",
                "Nada en especial",
                {
                    "tema": "conversacion_general",
                    "objetivo": "conversar_con_claridad",
                    "modo": "conversacion",
                },
                {"modo": "Sueño"},
            )

        self.assertFalse(decision.intervenir)


if __name__ == "__main__":
    unittest.main()