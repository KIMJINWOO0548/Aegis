cmake_minimum_required(VERSION 3.24)
project(AegisNative VERSION 0.2.0 LANGUAGES C CXX)

if(NOT WIN32 OR NOT MSVC)
    message(FATAL_ERROR "Aegis.Native targets Windows x64 with the MSVC ABI.")
endif()

include(CTest)

add_library(aegis_native SHARED src/native_engine.cpp)
target_include_directories(aegis_native PUBLIC include)
target_compile_features(aegis_native PRIVATE cxx_std_20)
target_compile_definitions(aegis_native PRIVATE AEGIS_NATIVE_BUILD WIN32_LEAN_AND_MEAN NOMINMAX)
set_target_properties(aegis_native PROPERTIES
    CXX_EXTENSIONS OFF
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES)
target_compile_options(aegis_native PRIVATE /W4 /permissive-)
set_property(TARGET aegis_native PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

install(TARGETS aegis_native RUNTIME DESTINATION . LIBRARY DESTINATION .)

if(BUILD_TESTING)
    add_executable(aegis_native_c_abi_test tests/c_abi_smoke.c)
    target_compile_features(aegis_native_c_abi_test PRIVATE c_std_11)
    target_link_libraries(aegis_native_c_abi_test PRIVATE aegis_native)
    add_test(NAME native.c_abi COMMAND aegis_native_c_abi_test)
    set_tests_properties(native.c_abi PROPERTIES TIMEOUT 10)

    add_executable(aegis_native_tests tests/native_engine_tests.cpp)
    target_compile_features(aegis_native_tests PRIVATE cxx_std_20)
    target_compile_definitions(aegis_native_tests PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    target_link_libraries(aegis_native_tests PRIVATE aegis_native)
    foreach(test IN ITEMS validation rolling_window discontinuities queue_drain cancel_and_wait concurrent_lifecycle)
        add_test(NAME native.${test} COMMAND aegis_native_tests ${test})
        set_tests_properties(native.${test} PROPERTIES TIMEOUT 30)
    endforeach()
endif()
