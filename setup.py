from setuptools import find_packages, setup

package_name = 'atom_krs_ros_udp_bridge'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='obamanarito',
    maintainer_email='obama1632@gmail.com',
    description='UDP bridge between mocube AtomS3 modules and ROS 2',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'device_manager = atom_krs_ros_udp_bridge.device_manager:main',
        ],
    },
)
