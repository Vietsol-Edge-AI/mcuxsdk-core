# Copyright 2024-2025 NXP
#
# SPDX-License-Identifier: BSD-3-Clause

if(CONFIG_MCUX_COMPONENT_driver.lpc_freqme)
    mcux_component_version(2.1.3)

    mcux_add_source(SOURCES fsl_freqme.h fsl_freqme.c)

    mcux_add_include(INCLUDES .)

endif()
