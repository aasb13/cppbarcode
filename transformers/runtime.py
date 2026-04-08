import state


def runtime_wrap_constant(expr_text: str) -> str:
    """Wrap expression in the runtime helper so optimizer can't fold it away."""
    state.init_runtime_obfuscation_names()
    return f"{state.RUNTIME_OBF_HELPER_NAME}({expr_text})"

