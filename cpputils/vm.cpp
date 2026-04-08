#include <cstdint>
#include <cstring>
#include <type_traits>
#include <cmath>
#include <tuple>
#include <limits>

namespace vm {
    // ========================================================================
    // Opcodes (Extended with FPU support)
    // ========================================================================
    enum Op : uint8_t {
        // Integer Stack operations
        PUSH_IMM32 = 0x01,      // push 32-bit immediate to integer stack
        PUSH_IMM64 = 0x02,      // push 64-bit immediate to integer stack
        PUSH_REG   = 0x03,      // push from virtual register to integer stack
        POP_REG    = 0x04,      // pop from integer stack to virtual register
        DUP        = 0x05,      // duplicate top of integer stack
        
        // Floating Point Stack operations (NEW)
        F_PUSH_IMM32 = 0x06,    // push 32-bit float immediate
        F_PUSH_IMM64 = 0x07,    // push 64-bit double immediate
        F_PUSH_REG   = 0x08,    // push from FP register to FP stack
        F_POP_REG    = 0x09,    // pop from FP stack to FP register
        F_DUP        = 0x0A,    // duplicate top of FP stack
        
        // Type conversion operations (NEW)
        I2F         = 0x0B,     // int64 -> float64 (pop int stack, push FP stack)
        F2I         = 0x0C,     // float64 -> int64 (pop FP stack, push int stack)
        I2F32       = 0x0D,     // int64 -> float32
        F322F64     = 0x0E,     // float32 -> float64
        F642F32     = 0x0F,     // float64 -> float32
        
        // Integer Memory operations
        LOAD8      = 0x10,      // load byte from [addr]
        LOAD16     = 0x11,      // load word
        LOAD32     = 0x12,      // load dword
        LOAD64     = 0x13,      // load qword
        STORE8     = 0x14,      // store byte to [addr]
        STORE16    = 0x15,      // store word
        STORE32    = 0x16,      // store dword
        STORE64    = 0x17,      // store qword
        
        // Floating Point Memory operations (NEW)
        F_LOAD32   = 0x18,      // load float from [addr] to FP stack
        F_LOAD64   = 0x19,      // load double from [addr] to FP stack
        F_STORE32  = 0x1A,      // store float from FP stack to [addr]
        F_STORE64  = 0x1B,      // store double from FP stack to [addr]
        
        // Integer Arithmetic
        ADD = 0x20, SUB = 0x21, MUL = 0x22, DIV = 0x23, MOD = 0x24,
        SHL = 0x25, SHR = 0x26, SAR = 0x27,
        
        // Floating Point Arithmetic (NEW)
        F_ADD = 0x28, F_SUB = 0x29, F_MUL = 0x2A, F_DIV = 0x2B,
        F_SQRT = 0x2C, F_SIN = 0x2D, F_COS = 0x2E, F_TAN = 0x2F,
        F_ATAN2 = 0x80, F_POW = 0x81, F_LOG = 0x82, F_EXP = 0x83,
        F_FLOOR = 0x84, F_CEIL = 0x85, F_ABS = 0x86,
        
        // Integer Bitwise
        AND = 0x30, OR = 0x31, XOR = 0x32, NOT = 0x33,
        
        // Integer Comparison
        EQ = 0x40, NE = 0x41, LT = 0x42, LE = 0x43, GT = 0x44, GE = 0x45,
        LT_U = 0x46, LE_U = 0x47, GT_U = 0x48, GE_U = 0x49,
        
        // Floating Point Comparison (NEW)
        F_EQ = 0x4A, F_NE = 0x4B, F_LT = 0x4C, F_LE = 0x4D, 
        F_GT = 0x4E, F_GE = 0x4F, F_ISNAN = 0x50,
        
        // Control flow
        JMP       = 0x51,       // relative jump
        JZ        = 0x52,       // jump if zero (integer stack)
        JNZ       = 0x53,       // jump if non-zero (integer stack)
        F_JZ      = 0x54,       // jump if FP stack top is 0.0 (NEW)
        F_JNZ     = 0x55,       // jump if FP stack top is not 0.0 (NEW)
        CALL_VM   = 0x56,       // call VM function (relative)
        RET       = 0x57,
        
        // External interface
        CALL_NATIVE = 0x60,     // call external function pointer
        SYSCALL     = 0x61,     // raw syscall
        CALL_NATIVE_PACKED = 0x63, // call bridge(int_args, int_count, fp_args, fp_count)
        
        // Debug/Metadata
        NOP      = 0x00,
        BREAK    = 0x62,        // trigger debug break (changed opcode)
        HALT     = 0xFF         // stop execution
    };

    // ========================================================================
    // Virtual Register File (with FPU registers)
    // ========================================================================
    struct RegFile {
        static constexpr size_t INT_COUNT = 32;
        static constexpr size_t FP_COUNT = 32;
        
        uint64_t r[INT_COUNT] = {0};     // Integer registers
        double   fr[FP_COUNT] = {0.0};   // Floating point registers
        
        // Special registers convention:
        // r[0]  = Zero register (always 0)
        // r[1]  = Integer return value
        // r[2]  = Stack pointer (for manual stack if needed)
        // r[3]  = Frame pointer
        // r[4-7]= Integer argument registers
        // r[8+]= General purpose
        
        // fr[0] = Constant 0.0
        // fr[1] = Float return value
        // fr[2] = Constant 1.0
        // fr[3-7] = Float argument registers
        // fr[8+] = General purpose float
    };

    // ========================================================================
    // Dual Stack VM State
    // ========================================================================
    struct State {
        // Execution context
        const uint8_t* code_base;
        size_t code_size;
        
        // Integer stack (grows up)
        static constexpr size_t INT_STACK_SIZE = 1024;
        uint64_t int_stack[INT_STACK_SIZE];
        uint64_t* int_sp;
        
        // Floating point stack (grows up) (NEW)
        static constexpr size_t FP_STACK_SIZE = 512;
        double fp_stack[FP_STACK_SIZE];
        double* fp_sp;
        
        // Virtual registers
        RegFile regs;
        
        // External function table
        void** native_table;
        uint32_t native_count;
        
        // Obfuscation state
        uint64_t key;
        uint64_t pc;  // Program counter (relative to code_base)
        uint64_t instruction_count;
        bool fp_return_set;
        
        // FPU control word emulation (rounding mode, exception masks)
        struct {
            uint8_t rounding_mode = 0;  // 0=nearest, 1=down, 2=up, 3=truncate
            bool fp_exception_pending = false;
        } fpu_control;
    };

    // ========================================================================
    // Decoder
    // ========================================================================
    inline uint8_t decode_byte(State& s, size_t offset) {
        return s.code_base[offset];  // NO ENCRYPTION for testing
    }

    // ========================================================================
    // Read helpers (handles endianness and decryption)
    // ========================================================================
    template<typename T>
    inline T read_imm(State& s) {
        T val = 0;
        for (size_t i = 0; i < sizeof(T); ++i) {
            val |= static_cast<T>(decode_byte(s, s.pc + i)) << (i * 8);
        }
        s.pc += sizeof(T);
        return val;
    }

    // ========================================================================
    // FPU Helper Functions
    // ========================================================================
    inline double apply_rounding(double val, uint8_t mode) {
        switch (mode) {
            case 1: return floor(val);      // down
            case 2: return ceil(val);       // up
            case 3: return trunc(val);      // truncate
            default: return val;            // preserve full precision by default
        }
    }

    // ========================================================================
    // Platform-specific debug break
    // ========================================================================
    inline void debug_break() {
        #ifdef _MSC_VER
            __debugbreak();
        #elif defined(__GNUC__) || defined(__clang__)
            __asm__ __volatile__("int3");
        #else
            __builtin_trap();
        #endif
    }

    // ========================================================================
    // Core Interpreter with FPU Support
    // ========================================================================
    __attribute__((noinline, optimize("O0"), noipa))
    uint64_t execute(const uint8_t* code, size_t size, uint64_t key,
                     uint64_t* int_args, uint32_t int_arg_count,
                     double* float_args, uint32_t float_arg_count,
                     void** native_funcs, uint32_t native_count) {
        
        State s;
        s.code_base = code;
        s.code_size = size;
        s.key = key;
        s.pc = 0;
        s.instruction_count = 0;
        s.fp_return_set = false;
        s.int_sp = s.int_stack;
        s.fp_sp = s.fp_stack;
        s.native_table = native_funcs;
        s.native_count = native_count;
        
        // Initialize registers
        for (size_t i = 0; i < RegFile::INT_COUNT; ++i) s.regs.r[i] = 0;
        for (size_t i = 0; i < RegFile::FP_COUNT; ++i) s.regs.fr[i] = 0.0;
        s.regs.fr[2] = 1.0;  // constant 1.0 register
        
        // Load integer arguments into r4-r7
        for (uint32_t i = 0; i < int_arg_count && i < 8; ++i) {
            s.regs.r[4 + i] = int_args[i];
        }
        
        // Load float arguments into fr3-fr7 (NEW)
        for (uint32_t i = 0; i < float_arg_count && i < 8; ++i) {
            s.regs.fr[3 + i] = float_args[i];
        }
        
        __asm__ __volatile__("" ::: "memory");
        
        // Main dispatch loop
        while (s.pc < s.code_size) {
            Op op = static_cast<Op>(decode_byte(s, s.pc++));
            s.instruction_count++;
            
            // Anti-debug: key mutation
            if ((s.instruction_count & 0x7F) == 0) {
                __asm__ __volatile__("" : "+r"(s.key) : : "memory");
                s.key ^= s.instruction_count;
                s.key = (s.key << 13) | (s.key >> 51);
            }
            
            switch (op) {
                // ============================================================
                // INTEGER STACK OPERATIONS
                // ============================================================
                case PUSH_IMM32: {
                    uint32_t val = read_imm<uint32_t>(s);
                    *s.int_sp++ = val;
                    break;
                }
                case PUSH_IMM64: {
                    uint64_t val = read_imm<uint64_t>(s);
                    *s.int_sp++ = val;
                    break;
                }
                case PUSH_REG: {
                    uint8_t idx = read_imm<uint8_t>(s);
                    *s.int_sp++ = (idx < RegFile::INT_COUNT) ? s.regs.r[idx] : 0;
                    break;
                }
                case POP_REG: {
                    uint8_t idx = read_imm<uint8_t>(s);
                    if (idx < RegFile::INT_COUNT && idx != 0) {
                        s.regs.r[idx] = *(--s.int_sp);
                    } else {
                        --s.int_sp;
                    }
                    break;
                }
                case DUP: {
                    uint64_t val = *(s.int_sp - 1);
                    *s.int_sp++ = val;
                    break;
                }
                
                // ============================================================
                // FLOATING POINT STACK OPERATIONS (NEW)
                // ============================================================
                case F_PUSH_IMM32: {
                    uint32_t bits = read_imm<uint32_t>(s);
                    float f;
                    std::memcpy(&f, &bits, sizeof(float));
                    *s.fp_sp++ = static_cast<double>(f);
                    break;
                }
                case F_PUSH_IMM64: {
                    uint64_t bits = read_imm<uint64_t>(s);
                    double d;
                    std::memcpy(&d, &bits, sizeof(double));
                    *s.fp_sp++ = d;
                    break;
                }
                case F_PUSH_REG: {
                    uint8_t idx = read_imm<uint8_t>(s);
                    *s.fp_sp++ = (idx < RegFile::FP_COUNT) ? s.regs.fr[idx] : 0.0;
                    break;
                }
                case F_POP_REG: {
                    uint8_t idx = read_imm<uint8_t>(s);
                    if (idx < RegFile::FP_COUNT && idx != 0) {
                        s.regs.fr[idx] = *(--s.fp_sp);
                        if (idx == 1) {
                            s.fp_return_set = true;
                        }
                    } else {
                        --s.fp_sp;
                    }
                    break;
                }
                case F_DUP: {
                    double val = *(s.fp_sp - 1);
                    *s.fp_sp++ = val;
                    break;
                }
                
                // ============================================================
                // TYPE CONVERSION OPERATIONS (NEW)
                // ============================================================
                case I2F: {
                    uint64_t int_val = *(--s.int_sp);
                    *s.fp_sp++ = static_cast<double>(static_cast<int64_t>(int_val));
                    break;
                }
                case F2I: {
                    double fp_val = *(--s.fp_sp);
                    *s.int_sp++ = static_cast<uint64_t>(static_cast<int64_t>(fp_val));
                    break;
                }
                case I2F32: {
                    uint64_t int_val = *(--s.int_sp);
                    float f = static_cast<float>(static_cast<int64_t>(int_val));
                    *s.fp_sp++ = static_cast<double>(f);
                    break;
                }
                case F322F64: {
                    uint32_t bits = static_cast<uint32_t>(*(--s.int_sp));
                    float f;
                    std::memcpy(&f, &bits, sizeof(float));
                    *s.fp_sp++ = static_cast<double>(f);
                    break;
                }
                case F642F32: {
                    double d = *(--s.fp_sp);
                    float f = static_cast<float>(d);
                    uint32_t bits;
                    std::memcpy(&bits, &f, sizeof(float));
                    *s.int_sp++ = bits;
                    break;
                }
                
                // ============================================================
                // INTEGER MEMORY OPERATIONS
                // ============================================================
                case LOAD8: {
                    uint64_t addr = *(--s.int_sp);
                    uint8_t val = *reinterpret_cast<const uint8_t*>(addr);
                    *s.int_sp++ = val;
                    break;
                }
                case LOAD16: {
                    uint64_t addr = *(--s.int_sp);
                    uint16_t val = *reinterpret_cast<const uint16_t*>(addr);
                    *s.int_sp++ = val;
                    break;
                }
                case LOAD32: {
                    uint64_t addr = *(--s.int_sp);
                    uint32_t val = *reinterpret_cast<const uint32_t*>(addr);
                    *s.int_sp++ = val;
                    break;
                }
                case LOAD64: {
                    uint64_t addr = *(--s.int_sp);
                    uint64_t val = *reinterpret_cast<const uint64_t*>(addr);
                    *s.int_sp++ = val;
                    break;
                }
                case STORE8: {
                    uint64_t val = *(--s.int_sp);
                    uint64_t addr = *(--s.int_sp);
                    *reinterpret_cast<uint8_t*>(addr) = static_cast<uint8_t>(val);
                    break;
                }
                case STORE16: {
                    uint64_t val = *(--s.int_sp);
                    uint64_t addr = *(--s.int_sp);
                    *reinterpret_cast<uint16_t*>(addr) = static_cast<uint16_t>(val);
                    break;
                }
                case STORE32: {
                    uint64_t val = *(--s.int_sp);
                    uint64_t addr = *(--s.int_sp);
                    *reinterpret_cast<uint32_t*>(addr) = static_cast<uint32_t>(val);
                    break;
                }
                case STORE64: {
                    uint64_t val = *(--s.int_sp);
                    uint64_t addr = *(--s.int_sp);
                    *reinterpret_cast<uint64_t*>(addr) = val;
                    break;
                }
                
                // ============================================================
                // FLOATING POINT MEMORY OPERATIONS (NEW)
                // ============================================================
                case F_LOAD32: {
                    uint64_t addr = *(--s.int_sp);
                    float f = *reinterpret_cast<const float*>(addr);
                    *s.fp_sp++ = static_cast<double>(f);
                    break;
                }
                case F_LOAD64: {
                    uint64_t addr = *(--s.int_sp);
                    double d = *reinterpret_cast<const double*>(addr);
                    *s.fp_sp++ = d;
                    break;
                }
                case F_STORE32: {
                    double d = *(--s.fp_sp);
                    uint64_t addr = *(--s.int_sp);
                    *reinterpret_cast<float*>(addr) = static_cast<float>(d);
                    break;
                }
                case F_STORE64: {
                    double d = *(--s.fp_sp);
                    uint64_t addr = *(--s.int_sp);
                    *reinterpret_cast<double*>(addr) = d;
                    break;
                }
                
                // ============================================================
                // INTEGER ARITHMETIC
                // ============================================================
                case ADD: { uint64_t b = *(--s.int_sp); s.int_sp[-1] += b; break; }
                case SUB: { uint64_t b = *(--s.int_sp); s.int_sp[-1] -= b; break; }
                case MUL: { uint64_t b = *(--s.int_sp); s.int_sp[-1] *= b; break; }
                case DIV: { 
                    uint64_t b = *(--s.int_sp); 
                    s.int_sp[-1] = (b != 0) ? s.int_sp[-1] / b : 0; 
                    break; 
                }
                case MOD: { 
                    uint64_t b = *(--s.int_sp); 
                    s.int_sp[-1] = (b != 0) ? s.int_sp[-1] % b : 0; 
                    break; 
                }
                case SHL: { uint64_t b = *(--s.int_sp); s.int_sp[-1] <<= b; break; }
                case SHR: { uint64_t b = *(--s.int_sp); s.int_sp[-1] >>= b; break; }
                case SAR: { 
                    uint64_t b = *(--s.int_sp); 
                    s.int_sp[-1] = static_cast<uint64_t>(static_cast<int64_t>(s.int_sp[-1]) >> b); 
                    break; 
                }
                
                // ============================================================
                // FLOATING POINT ARITHMETIC (NEW)
                // ============================================================
                case F_ADD: { 
                    double b = *(--s.fp_sp); 
                    s.fp_sp[-1] = apply_rounding(s.fp_sp[-1] + b, s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_SUB: { 
                    double b = *(--s.fp_sp); 
                    s.fp_sp[-1] = apply_rounding(s.fp_sp[-1] - b, s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_MUL: { 
                    double b = *(--s.fp_sp); 
                    s.fp_sp[-1] = apply_rounding(s.fp_sp[-1] * b, s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_DIV: { 
                    double b = *(--s.fp_sp); 
                    s.fp_sp[-1] = (b != 0.0) ? apply_rounding(s.fp_sp[-1] / b, s.fpu_control.rounding_mode) : 
                                              std::numeric_limits<double>::quiet_NaN(); 
                    break; 
                }
                case F_SQRT: { 
                    s.fp_sp[-1] = apply_rounding(sqrt(s.fp_sp[-1]), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_SIN: { 
                    s.fp_sp[-1] = apply_rounding(sin(s.fp_sp[-1]), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_COS: { 
                    s.fp_sp[-1] = apply_rounding(cos(s.fp_sp[-1]), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_TAN: { 
                    s.fp_sp[-1] = apply_rounding(tan(s.fp_sp[-1]), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_ATAN2: { 
                    double x = *(--s.fp_sp); 
                    double y = *(--s.fp_sp); 
                    *s.fp_sp++ = apply_rounding(atan2(y, x), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_POW: { 
                    double exp = *(--s.fp_sp); 
                    double base = s.fp_sp[-1]; 
                    s.fp_sp[-1] = apply_rounding(pow(base, exp), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_LOG: { 
                    s.fp_sp[-1] = apply_rounding(log(s.fp_sp[-1]), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_EXP: { 
                    s.fp_sp[-1] = apply_rounding(exp(s.fp_sp[-1]), s.fpu_control.rounding_mode); 
                    break; 
                }
                case F_FLOOR: { 
                    s.fp_sp[-1] = floor(s.fp_sp[-1]); 
                    break; 
                }
                case F_CEIL: { 
                    s.fp_sp[-1] = ceil(s.fp_sp[-1]); 
                    break; 
                }
                case F_ABS: { 
                    s.fp_sp[-1] = fabs(s.fp_sp[-1]); 
                    break; 
                }
                
                // ============================================================
                // INTEGER BITWISE
                // ============================================================
                case AND: { uint64_t b = *(--s.int_sp); s.int_sp[-1] &= b; break; }
                case OR:  { uint64_t b = *(--s.int_sp); s.int_sp[-1] |= b; break; }
                case XOR: { uint64_t b = *(--s.int_sp); s.int_sp[-1] ^= b; break; }
                case NOT: { s.int_sp[-1] = ~s.int_sp[-1]; break; }
                
                // ============================================================
                // INTEGER COMPARISON
                // ============================================================
                case EQ:  { uint64_t b = *(--s.int_sp); s.int_sp[-1] = (s.int_sp[-1] == b); break; }
                case NE:  { uint64_t b = *(--s.int_sp); s.int_sp[-1] = (s.int_sp[-1] != b); break; }
                case LT:  { 
                    int64_t b = static_cast<int64_t>(*(--s.int_sp));
                    s.int_sp[-1] = (static_cast<int64_t>(s.int_sp[-1]) < b); 
                    break; 
                }
                case LE:  { 
                    int64_t b = static_cast<int64_t>(*(--s.int_sp));
                    s.int_sp[-1] = (static_cast<int64_t>(s.int_sp[-1]) <= b); 
                    break; 
                }
                case GT:  { 
                    int64_t b = static_cast<int64_t>(*(--s.int_sp));
                    s.int_sp[-1] = (static_cast<int64_t>(s.int_sp[-1]) > b); 
                    break; 
                }
                case GE:  { 
                    int64_t b = static_cast<int64_t>(*(--s.int_sp));
                    s.int_sp[-1] = (static_cast<int64_t>(s.int_sp[-1]) >= b); 
                    break; 
                }
                case LT_U: { uint64_t b = *(--s.int_sp); s.int_sp[-1] = (s.int_sp[-1] < b); break; }
                case LE_U: { uint64_t b = *(--s.int_sp); s.int_sp[-1] = (s.int_sp[-1] <= b); break; }
                case GT_U: { uint64_t b = *(--s.int_sp); s.int_sp[-1] = (s.int_sp[-1] > b); break; }
                case GE_U: { uint64_t b = *(--s.int_sp); s.int_sp[-1] = (s.int_sp[-1] >= b); break; }
                
                // ============================================================
                // FLOATING POINT COMPARISON (NEW)
                // ============================================================
                case F_EQ: { 
                    double b = *(--s.fp_sp); 
                    *s.int_sp++ = (s.fp_sp[-1] == b) ? 1 : 0; 
                    --s.fp_sp; // pop a
                    break; 
                }
                case F_NE: { 
                    double b = *(--s.fp_sp); 
                    *s.int_sp++ = (s.fp_sp[-1] != b) ? 1 : 0; 
                    --s.fp_sp; 
                    break; 
                }
                case F_LT: { 
                    double b = *(--s.fp_sp); 
                    *s.int_sp++ = (s.fp_sp[-1] < b) ? 1 : 0; 
                    --s.fp_sp; 
                    break; 
                }
                case F_LE: { 
                    double b = *(--s.fp_sp); 
                    *s.int_sp++ = (s.fp_sp[-1] <= b) ? 1 : 0; 
                    --s.fp_sp; 
                    break; 
                }
                case F_GT: { 
                    double b = *(--s.fp_sp); 
                    *s.int_sp++ = (s.fp_sp[-1] > b) ? 1 : 0; 
                    --s.fp_sp; 
                    break; 
                }
                case F_GE: { 
                    double b = *(--s.fp_sp); 
                    *s.int_sp++ = (s.fp_sp[-1] >= b) ? 1 : 0; 
                    --s.fp_sp; 
                    break; 
                }
                case F_ISNAN: { 
                    *s.int_sp++ = std::isnan(s.fp_sp[-1]) ? 1 : 0; 
                    --s.fp_sp; 
                    break; 
                }
                
                // ============================================================
                // CONTROL FLOW
                // ============================================================
                case JMP: {
                    int32_t offset = static_cast<int32_t>(read_imm<uint32_t>(s));
                    s.pc += offset;
                    break;
                }
                case JZ: {
                    int32_t offset = static_cast<int32_t>(read_imm<uint32_t>(s));
                    uint64_t cond = *(--s.int_sp);
                    if (cond == 0) s.pc += offset;
                    break;
                }
                case JNZ: {
                    int32_t offset = static_cast<int32_t>(read_imm<uint32_t>(s));
                    uint64_t cond = *(--s.int_sp);
                    if (cond != 0) s.pc += offset;
                    break;
                }
                case F_JZ: {
                    int32_t offset = static_cast<int32_t>(read_imm<uint32_t>(s));
                    double cond = *(--s.fp_sp);
                    if (cond == 0.0) s.pc += offset;
                    break;
                }
                case F_JNZ: {
                    int32_t offset = static_cast<int32_t>(read_imm<uint32_t>(s));
                    double cond = *(--s.fp_sp);
                    if (cond != 0.0) s.pc += offset;
                    break;
                }
                case CALL_VM: {
                    int32_t offset = static_cast<int32_t>(read_imm<uint32_t>(s));
                    *s.int_sp++ = s.pc;  // push return address
                    s.pc += offset;
                    break;
                }
                case RET: {
                    if (s.int_sp > s.int_stack) {
                        s.pc = *(--s.int_sp);
                    } else {
                        goto exit_loop;
                    }
                    break;
                }
                
                // ============================================================
                // EXTERNAL INTERFACE
                // ============================================================
                case CALL_NATIVE: {
                    uint32_t func_idx = read_imm<uint32_t>(s);
                    uint32_t arg_count = read_imm<uint32_t>(s);
                    
                    if (func_idx < s.native_count && s.native_table[func_idx] != nullptr) {
                        uint64_t args[16];
                        for (uint32_t i = 0; i < arg_count && i < 16; ++i) {
                            args[arg_count - 1 - i] = *(--s.int_sp);
                        }
                        
                        using NativeFunc = uint64_t(*)(...);
                        NativeFunc func = reinterpret_cast<NativeFunc>(s.native_table[func_idx]);
                        
                        uint64_t result = 0;
                        switch (arg_count) {
                            case 0: result = func(); break;
                            case 1: result = func(args[0]); break;
                            case 2: result = func(args[0], args[1]); break;
                            case 3: result = func(args[0], args[1], args[2]); break;
                            case 4: result = func(args[0], args[1], args[2], args[3]); break;
                            default: break;
                        }
                        
                        *s.int_sp++ = result;
                        s.regs.r[1] = result;
                    }
                    break;
                }
                case CALL_NATIVE_PACKED: {
                    uint32_t func_idx = read_imm<uint32_t>(s);
                    uint32_t int_arg_count = read_imm<uint32_t>(s);
                    uint32_t fp_arg_count = read_imm<uint32_t>(s);
                    uint8_t return_kind = read_imm<uint8_t>(s);

                    if (func_idx < s.native_count && s.native_table[func_idx] != nullptr) {
                        uint64_t int_args[16] = {0};
                        double fp_args[16] = {0.0};
                        for (uint32_t i = 0; i < int_arg_count && i < 16; ++i) {
                            int_args[int_arg_count - 1 - i] = *(--s.int_sp);
                        }
                        for (uint32_t i = 0; i < fp_arg_count && i < 16; ++i) {
                            fp_args[fp_arg_count - 1 - i] = *(--s.fp_sp);
                        }

                        using BridgeFunc = uint64_t(*)(const uint64_t*, uint64_t, const double*, uint64_t);
                        BridgeFunc func = reinterpret_cast<BridgeFunc>(s.native_table[func_idx]);
                        uint64_t result = func(int_args, int_arg_count, fp_args, fp_arg_count);

                        if (return_kind == 1) {
                            double fp_result = 0.0;
                            std::memcpy(&fp_result, &result, sizeof(fp_result));
                            *s.fp_sp++ = fp_result;
                            s.regs.fr[1] = fp_result;
                            s.fp_return_set = true;
                        } else {
                            *s.int_sp++ = result;
                            s.regs.r[1] = result;
                        }
                    }
                    break;
                }
                
                case NOP:
                    break;
                    
                case BREAK:
                    debug_break();
                    break;
                    
                case HALT:
                    goto exit_loop;
                    
                default:
                    __builtin_trap();
                    break;
            }
        }
        
    exit_loop:
        // Check if result should come from FP stack or integer stack
        uint64_t result;
        
        // Prefer an explicit floating-point result from the FP stack,
        // then the FP return register, before falling back to integer results.
        if (s.fp_sp > s.fp_stack) {
            double fp_result = *(s.fp_sp - 1);
            // Return the raw bits as uint64_t
            std::memcpy(&result, &fp_result, sizeof(result));
        } else if (s.fp_return_set) {
            double fp_result = s.regs.fr[1];
            std::memcpy(&result, &fp_result, sizeof(result));
        } else if (s.int_sp > s.int_stack) {
            result = *(s.int_sp - 1);
        } else {
            result = s.regs.r[1];
        }
        
        __asm__ __volatile__("" : "+r"(result) : : "memory");
        return result;
    }

    // ========================================================================
    // Helper for tuple expansion
    // ========================================================================
    template<typename Tuple, std::size_t... Is>
    auto tuple_to_array_impl(const Tuple& t, std::index_sequence<Is...>) {
        return std::array<double, sizeof...(Is)>{ static_cast<double>(std::get<Is>(t))... };
    }
    
    template<typename... Args>
    auto tuple_to_array(const std::tuple<Args...>& t) {
        return tuple_to_array_impl(t, std::index_sequence_for<Args...>{});
    }

    // ========================================================================
    // User-friendly wrappers
    // ========================================================================
    template<typename R, typename... Args>
    R run_int(const uint8_t* code, size_t size, uint64_t key, 
              void** native_funcs, uint32_t native_count, Args... args) {
        
        uint64_t arg_array[] = { static_cast<uint64_t>(args)... };
        uint64_t result = execute(code, size, key, arg_array, sizeof...(Args),
                                   nullptr, 0, native_funcs, native_count);
        return static_cast<R>(result);
    }
    
    template<typename R, typename... IArgs, typename... FArgs>
    R run_mixed(const uint8_t* code, size_t size, uint64_t key,
                void** native_funcs, uint32_t native_count,
                const std::tuple<IArgs...>& int_args,
                const std::tuple<FArgs...>& float_args) {
        
        // Convert int tuple to array
        constexpr size_t int_count = sizeof...(IArgs);
        uint64_t int_arg_array[int_count > 0 ? int_count : 1] = {0};
        if constexpr (int_count > 0) {
            size_t i = 0;
            std::apply([&](auto... vals) {
                ((int_arg_array[i++] = static_cast<uint64_t>(vals)), ...);
            }, int_args);
        }
        
        // Convert float tuple to array
        constexpr size_t float_count = sizeof...(FArgs);
        double float_arg_array[float_count > 0 ? float_count : 1] = {0.0};
        if constexpr (float_count > 0) {
            size_t i = 0;
            std::apply([&](auto... vals) {
                ((float_arg_array[i++] = static_cast<double>(vals)), ...);
            }, float_args);
        }
        
        uint64_t result = execute(code, size, key, 
                                   int_arg_array, int_count,
                                   float_arg_array, float_count,
                                   native_funcs, native_count);
        if constexpr (std::is_floating_point_v<R>) {
            double d;
            std::memcpy(&d, &result, sizeof(d));
            return static_cast<R>(d);
        } else {
            return static_cast<R>(result);
        }
    }

    // ========================================================================
    // Simple wrapper for float-only functions
    // ========================================================================
    template<typename R>
    inline R convert_result(uint64_t val) {
        if constexpr (std::is_floating_point_v<R>) {
            double d;
            std::memcpy(&d, &val, sizeof(d));
            return static_cast<R>(d);
        } else {
            return static_cast<R>(val);
        }
    }

    template<typename R, typename... Args>
    R run_float(const uint8_t* code, size_t size, uint64_t key,
                void** native_funcs, uint32_t native_count, Args... args) {
        
        double arg_array[] = { static_cast<double>(args)... };
        uint64_t result = execute(code, size, key, nullptr, 0,
                                arg_array, sizeof...(Args),
                                native_funcs, native_count);
        return convert_result<R>(result);
    }

    // ========================================================================
    // Helper: Create external function table
    // ========================================================================
    struct NativeTable {
        static constexpr size_t MAX_FUNCS = 64;
        void* funcs[MAX_FUNCS];
        size_t count = 0;
        
        template<typename F>
        void add(F* func) {
            if (count < MAX_FUNCS) {
                funcs[count++] = reinterpret_cast<void*>(func);
            }
        }
    };
} // namespace vm
