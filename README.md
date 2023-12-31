# Michelin Rated Restaurants in France: An Analysis
![Michelin Star](Images/Etoile_Michelin.svg)

---

## Overview
This project is multifaceted. While it showcases proficiency in data acquisition, processing, and visualisation, it also delves into the world of Michelin-rated restaurants in France. Leveraging datasets from the Michelin Guide and INSEE, along with French geospatial data, this analysis probes the correlation between elite restaurants and the socio-economic attributes of their surroundings.

Previously I was a Michelin trained chef and lived in France for six transformative years. Those years, rich from the diverse cultural tapestry of France, have instilled in me a deep appreciation for the intricate world of gastronomy and its profound influence on local cultures. This project is not just an analytical endeavor but a personal journey. Through it, I seek to marry my experiences with my current passion for data, aiming to uncover insights that resonate on both a professional and personal level.


---

## Project Objective

This analysis seeks to address the question:

*"How do Michelin-starred establishments correlate with the socio-economic metrics of their respective regions?"* 

The aim is to better understand the intricate relationship between culinary excellence, its geographical distribution, and the socio-economic fabric of France.

---

## Data Sources
- The [Michelin Guide Restaurants](https://www.kaggle.com/datasets/ngshiheng/michelin-guide-restaurants-2021) data has been sourced from Kaggle, a platform for predictive modelling and analytics competitions.
- Demographic and socio-economic data was sourced from [INSEE](https://www.insee.fr/fr/accueil) (Institut National de la Statistique et des Études Économiques), the National Institute of Statistics and Economic Studies. It is responsible for the production and analysis of official French statistics.
- The geospatial data used in this analysis is sourced from [France-GeoJSON](https://france-geojson.gregoiredavid.fr). We acknowledge and thank the contributors and maintainers of this resource for making it publicly available.

---

## Data Aggregation and Processing
Processing utilizes the unique feature of the French postal code, whereby the initial two digits denote a specific department. 

France's administrative divisions are structured hierarchically:

- Regions (Régions): The top-level division, with mainland France having 13 regions.
- Departments (Départements): Each region is subdivided into departments, with 96 in mainland France.
- Arrondissements: Departments are further divided into arrondissements for administrative purposes, with 320 in mainland France, excluding the 20 municipal arrondissements of Paris.

Data processing involved four Jupyter notebooks:

1. [`Data-Preparation.ipynb`](Notebooks/Data-Preparation.ipynb): The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_data.csv` was subsequently exported.

2. [`France_Departments_Regions.ipynb`](Notebooks/France/France_Departments_Regions.ipynb): This notebook was used to aggregate socio-economic data from different sources. Departmental and regional data were scraped from Wikipedia, whereas population statistics were fetched from INSEE. 
   - The output `demographics.csv` was exported.

3. [`France_Arrondissements.ipynb`](Notebooks/France/France_Arrondissements.ipynb): The data was further granulated to *arrondissement* level

4. [`France_Processing.ipynb`](Notebooks/France/France_Processing.ipynb): This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the socio-economic and geospatial JSON data.

After the processing, the following datasets were exported:

- `all_restaurants.csv` - Michelin restaurant data appended with regional information.
- `arrondissement_restaurants.geojson` - A combination of Michelin data and arrondissement GeoJSON data
- `department_restaurants.geojson` - A combination of Michelin data and departmental GeoJSON data.
- `region_restaurants.geojson` - A combination of Michelin data and regional GeoJSON data.

---

## [Visualisations](Notebooks/France/France_Visualisations.ipynb)
Intricate functions are presented and [defined](Functions/functions_visualisation.py) to query and represent the data in diverse and insightful manners. 

---

## [Analysis](Notebooks/France/France_Analysis.ipynb)
The analysis segment of this project is designed for both technical and non-technical audiences. Utilizing the functions from the visualisation phase, this section endeavors to answer pertinent questions with minimal code to ensure readability and comprehension.

---
## [Interactive Application](https://michelin-france-2ed085da7e1c.herokuapp.com)
An interactive application built using Dash and Plotly provides a dynamic interface for users to explore Michelin-rated establishments in France. Users can select specific regions, zoom into departments, and view detailed restaurant information.

Access the code: [michelin_app.py](App/michelin_app.py)

---


## Future Updates
Given the Michelin Guide's yearly release schedule, this project intends to periodically update the analysis to reflect the latest trends and insights. Automation structures for these updates are currently under consideration.

---

## Installation Guide

### 1. Create and Activate a Virtual Environment
```bash
python3 -m venv env
source env/bin/activate
```

### 2. Install the required packages
```bash
pip install -r requirements.txt
```

### 3. Deactivate the virtual environment (when done)
```bash
deactivate
```
----

