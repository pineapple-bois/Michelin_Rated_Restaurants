import plotly.express as px
import plotly.graph_objects as go
import json


def plot_interactive_department(data_df, geo_df, department_code):
    # Filter the data based on the department code and exclude 0.5-star restaurants
    geo_df = json.loads(geo_df.to_json())
    dept_data = data_df[(data_df['department_num'] == str(department_code)) & (data_df['stars'] != 0.5)].copy()

    # Define custom color map based on stars
    color_map = {1.0: "yellow", 2.0: "orange", 3.0: "red"}
    dept_data['color'] = dept_data['stars'].map(color_map)

    # Construct hover text with clickable URL and repeated star emojis
    dept_data['hover_text'] = dept_data.apply(lambda row: f"<b>{row['name']}</b><br>{'⭐' * int(row['stars'])}<br>"
                                                          f"City: {row['city']}<br>Cuisine: {row['cuisine']}<br>"
                                                          f"<a href='{row['url']}' target='_blank'>Visit website</a><br>"
                                                          f"Price: {row['price']}",
                                              axis=1)

    # Extract the geoJSON for the specific department
    specific_geoJSON = {
        "type": "FeatureCollection",
        "features": [feature for feature in geo_df['features'] if feature['properties']['code'] == str(department_code)]
    }

    # Create a map with a base boundary layer for the department
    fig = go.Figure(go.Choroplethmapbox(geojson=specific_geoJSON,
                                        locations=dept_data['department_num'],
                                        z=[1]*len(dept_data),  # same value to all for consistent color
                                        colorscale=[[0, 'lightgrey'], [1, 'lightgrey']],  # constant color
                                        showscale=False,  # hide the colorbar
                                        hoverinfo='none'))  # turn off hover

    # Overlay restaurant points
    for star, color in color_map.items():
        subset = dept_data[dept_data['stars'] == star]
        fig.add_trace(go.Scattermapbox(lat=subset['latitude'],
                                       lon=subset['longitude'],
                                       mode='markers',
                                       marker=go.scattermapbox.Marker(size=10, color=color),
                                       text=subset['hover_text'],
                                       hovertemplate='%{text}<br>Coordinates: (%{lat}, %{lon})',  # custom hover template
                                       name=f"{'⭐' * int(star)}"))  # set legend name to repeated star emojis
    # Adjusting layout
    fig.update_layout(
        width=1000,
        height=800,
        mapbox_style="carto-positron",
        mapbox_zoom=8,
        mapbox_center_lat=dept_data['latitude'].mean(),
        mapbox_center_lon=dept_data['longitude'].mean()
    )

    fig.show()


