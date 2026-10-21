/* Compile-time wrapper used by compile_shaders_fsr4.sh. */
#include "fsr4_spirv_compat.hlsli"

#ifndef Q2RTX_FSR4_SHADER_SOURCE
#error Q2RTX_FSR4_SHADER_SOURCE must name the SDK shader to compile
#endif

#include Q2RTX_FSR4_SHADER_SOURCE
