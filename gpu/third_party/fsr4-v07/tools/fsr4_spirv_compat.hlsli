/*
 * Vulkan-only source compatibility for the FSR4 INT8 shaders.
 *
 * The Windows DXC distributed with FidelityFX SDK 1.1.4 understands the
 * Shader Model 6.4 dot intrinsics when producing DXIL, but its SPIR-V backend
 * reports them as unimplemented.  Expressing the packed signed dot product as
 * an inline SPIR-V instruction keeps the hardware DOT4 path intact.  The
 * dot2add replacement is the same half-multiply/float-accumulate sequence used
 * by current upstream DXC's SPIR-V emitter.
 *
 * This file is force-included only by compile_shaders_fsr4.sh when producing
 * SPIR-V.  It does not modify the SDK shader sources or their DXIL build.
 */

#ifndef Q2RTX_FSR4_SPIRV_COMPAT_HLSLI
#define Q2RTX_FSR4_SPIRV_COMPAT_HLSLI

[[vk::ext_capability(6019)]] /* DotProduct */
[[vk::ext_capability(6018)]] /* DotProductInput4x8BitPacked */
[[vk::ext_extension("SPV_KHR_integer_dot_product")]]
[[vk::ext_instruction(4450)]] /* OpSDot */
int q2rtx_fsr4_sdot4_packed(uint lhs, uint rhs,
                            [[vk::ext_literal]] uint packed_vector_format);

/* PackedVectorFormat4x8Bit is enumerant zero. */
#define dot4add_i8packed(lhs, rhs, accumulator) \
    (q2rtx_fsr4_sdot4_packed((lhs), (rhs), 0) + (accumulator))

/* Match DXC's processIntrinsicDP2a(): multiply as half, convert each product
 * to float, add the two lanes, then add the float accumulator. */
#define dot2add(lhs, rhs, accumulator) \
    ((float)((lhs).x * (rhs).x) + (float)((lhs).y * (rhs).y) + (accumulator))

#endif
