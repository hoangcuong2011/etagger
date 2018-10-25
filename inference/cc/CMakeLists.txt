cmake_minimum_required( VERSION 2.8 )

project( TFCustomOp )

list(APPEND CMAKE_MODULE_PATH ${PROJECT_SOURCE_DIR}/../cmake/modules)
find_package(TensorFlow 1.11 EXACT REQUIRED)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -D_GLIBCXX_USE_CXX11_ABI=${TensorFlow_ABI}")
set(CMAKE_EXE_LINKER_FLAGS  "${CMAKE_EXE_LINKER_FLAGS} -D_GLIBCXX_USE_CXX11_ABI=${TensorFlow_ABI}" )

TensorFlow_REQUIRE_C_LIBRARY()
TensorFlow_REQUIRE_SOURCE()

add_executable (inference_example inference_example.cc)
target_include_directories(inference_example PRIVATE TensorFlow_DEP)
target_link_libraries(inference_example PRIVATE TensorFlow_DEP)

add_executable (inference_iris inference_iris.cc)
target_include_directories(inference_iris PRIVATE TensorFlow_DEP)
target_link_libraries(inference_iris PRIVATE TensorFlow_DEP)