"""Prueba local del resolutor: no toca Groq, MySQL, red ni la Omen."""

from intenciones_deterministas import resolver_intencion_local


def comprobar(texto, sesion, esperado_tipo, esperado_comando=None):
    resultado = resolver_intencion_local(texto, sesion)
    print(f"{texto!r} -> {resultado}")
    assert resultado is not None, "La orden debería tener una decisión local"
    assert resultado["tipo"] == esperado_tipo
    if esperado_comando:
        assert resultado["comando"] == esperado_comando


def main():
    # Una charla normal sigue sin tocarse.
    assert resolver_intencion_local("hola, Deisy", "charla") is None

    comprobar(
        "sube el volumen",
        "volumen",
        "comando",
        "sistema: subir_volumen",
    )
    comprobar(
        "baja el volumen",
        "volumen",
        "comando",
        "sistema: bajar_volumen",
    )

    # El apagado exige objetivo y confirmación dentro de la MISMA sesión.
    comprobar("apaga el ordenador", "apagado", "respuesta")
    comprobar("Omen", "apagado", "respuesta")
    comprobar(
        "confirmar apagado Omen",
        "apagado",
        "comando",
        "sistema: apagar_pc",
    )

    # «Sí» sin un apagado previo no puede apagar nada.
    assert resolver_intencion_local("sí", "sin_estado") is None
    print("\n✅ Prueba local correcta: todavía no se ha ejecutado ninguna acción.")


if __name__ == "__main__":
    main()
