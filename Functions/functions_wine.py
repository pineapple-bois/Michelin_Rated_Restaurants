import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import geopandas as gpd
import numpy as np
import pyproj

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import geopandas as gpd
import numpy as np
import pyproj


def plot_choropleth_wine(df, wine_gdf, title, restaurants=False, star_rating=None, figsize=(10, 10)):
    """
    Function to plot a base map and optionally wine regions and restaurant locations.

    Args:
        df (GeoDataFrame): The DataFrame containing the base data.
        wine_gdf (GeoDataFrame): GeoDataFrame with wine regions.
        title (str): The title of the plot.
        restaurants (bool): If True, plot restaurant locations. Default is False.
        star_rating (int): Default is None = 'all'.
        figsize (tuple): The size of the figure. Default is (10, 10).

    Returns:
        None
    """

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    df = df.to_crs("EPSG:2154")  # RGF93 / Lambert-93
    df.boundary.plot(linewidth=0.8, edgecolor='0.8', ax=ax)

    # Process the wine GeoDataFrame
    wine_gdf = wine_gdf.to_crs("EPSG:2154")
    union_geometry = df.unary_union
    wine_gdf = wine_gdf[wine_gdf.geometry.intersects(union_geometry)]
    wine_gdf.plot(ax=ax, color=wine_gdf['colours'])

    all_handles = []
    added_labels = set()

    # Plot restaurants if needed
    if restaurants:
        star_colors = {'1': 'green', '2': 'orange', '3': 'red'}
        transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)

        if star_rating is not None:
            star_colors = {str(star_rating): star_colors[str(star_rating)]}

        for star, color in star_colors.items():
            for _, row in df.iterrows():
                locations = row['locations']
                if star in locations and locations[star] is not None:
                    for lat, lon in locations[star]:
                        x, y = transformer.transform(lon, lat)
                        ax.scatter(x, y, c=color, s=50, marker='d')

            if star not in added_labels:
                all_handles.append(
                    Line2D([0], [0], marker='d', color=color, label=f"{star} star restaurant", markersize=8,
                           linestyle='None'))
                added_labels.add(star)

    # Add legend for wine regions
    wine_colors = wine_gdf['colours'].unique()
    wine_regions = [wine_gdf[wine_gdf['colours'] == color]['region'].iloc[0] for color in wine_colors]
    wine_legend_handles = [Patch(facecolor=color, edgecolor=color, label=region) for region, color in
                           zip(wine_regions, wine_colors)]
    all_handles.extend(wine_legend_handles)

    ax.legend(handles=all_handles, loc='upper left', bbox_to_anchor=(1.05, 1), title="Legend", borderaxespad=0.)

    plt.suptitle(title)
    ax.set_yticklabels([])
    ax.set_xticklabels([])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.axis('off')

    plt.tight_layout()
    plt.show()