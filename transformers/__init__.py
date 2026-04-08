class IncludeWrappingTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class DefineObfuscationTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class ControlFlowFlatteningTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class CFGPollutionTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class OpaquePredicateTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class DeadCodeBlockTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class DeadCodeRemovalTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class AstRewriteTransformer:
    def __init__(self, collect_fn):
        self.collect = collect_fn


class RuntimeHelperTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class FloatingConstantHelperTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class ControlBodyBracingTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class GotoFlowTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class ThrowFlowTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class CherryFlowTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class TemplateExplosionTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class STLWrapperTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class DeadCodeHelperTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class TMPAdditionTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class FunctionPointerIndirectionTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class FunctionCloningTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class FunctionReorderingTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class StatementReorderingTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class LoopIdiomTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class DataFlowTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class StylometricNoiseTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class WhitespaceDegradationTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class StringLiteralEncryptionTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class MemoryAccessTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class TypeLevelObfuscationTransformer:
    def __init__(self, apply_fn):
        self.apply = apply_fn


class VirtualMachineTransformer:
    def __init__(self, collect_fn, inject_fn):
        self.collect = collect_fn
        self.inject = inject_fn
