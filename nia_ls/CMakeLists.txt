cmake_minimum_required(VERSION 3.16)

project(nia_ls_main LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(nia_ls_main
  src/main.cpp
  src/nia_ls.cpp
  src/nia_build_instance.cpp
  src/nia_initialize.cpp
  src/nia_print.cpp
)

target_include_directories(nia_ls_main PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
)

if(MSVC)
  target_compile_options(nia_ls_main PRIVATE /W3)
else()
  target_compile_options(nia_ls_main PRIVATE -O2)
endif()
