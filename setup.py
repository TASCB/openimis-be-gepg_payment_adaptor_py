import os
from setuptools import find_packages, setup

with open(os.path.join(os.path.dirname(__file__), 'README.md')) as readme:
    README = readme.read()

os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name='openimis-be-gepg_payment_adaptor',
    version='1.0.0',
    packages=find_packages(),
    include_package_data=True,
    license='GNU AGPL v3',
    description='The openIMIS Backend gepg_payment_adaptor module.',
    long_description=README,
    long_description_content_type='text/markdown',
    url='https://openimis.org/',
    install_requires=[
        'django',
        'django-db-signals',
        'djangorestframework',
        'celery',
        'openimis-be-core',
        'openimis-be-payroll',
        'openimis-be-tasaf_payment',
        'openimis-be-coremis_app_integration',  # shared GovESB transport
    ],
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
    ],
)
