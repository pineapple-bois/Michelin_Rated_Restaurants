# Michelin Guide to France 2023

Data processing involved four Jupyter notebooks:

1. [`Data-Preparation.ipynb`](../../Years/2023/Notebooks/Data-Preparation.ipynb): The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_data.csv` was subsequently exported.

2. [`France_Departments_Regions.ipynb`](../../Years/2023/Notebooks/France/France_Departments_Demographics.ipynb): This notebook was used to aggregate socio-economic data from different sources. Departmental and regional data were scraped from Wikipedia, whereas population statistics were fetched from INSEE. 
   - The output `demographics.csv` was exported.

3. [`France_Arrondissements.ipynb`](../../Years/2023/Notebooks/France/France_Arrondissements.ipynb): The data was further granulated to *arrondissement* level

4. [`France_Processing.ipynb`](../../Years/2023/Notebooks/France/France_Processing.ipynb): This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the socio-economic and geospatial JSON data.