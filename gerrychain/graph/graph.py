import json
import warnings

import geopandas as gp
import networkx
from networkx.readwrite import json_graph
from shapely.ops import unary_union
from shapely.prepared import prep

from .adjacency import neighbors
from .geo import reprojected


class Graph(networkx.Graph):
    """Represents a graph to be partitioned. It is based on :class:`networkx.Graph`.

    We have added some classmethods to help construct graphs from shapefiles, and
    to save and load graphs as JSON files.
    """

    @classmethod
    def from_json(cls, json_file):
        """Load a graph from a JSON file in the NetworkX json_graph format.
        :param json_file: Path to JSON file.
        :return: Graph
        """
        with open(json_file) as f:
            data = json.load(f)
        g = json_graph.adjacency_graph(data)
        graph = cls(g)
        graph.issue_warnings()
        return graph

    def to_json(self, json_file, *, include_geometries_as_geojson=False):
        """Save a graph to a JSON file in the NetworkX json_graph format.
        :param json_file: Path to target JSON file.
        :param bool include_geometry_as_geojson: (optional) Whether to include any
            :mod:`shapely` geometry objects encountered in the graph's node attributes
            as GeoJSON. The default (``False``) behavior is to remove all geometry
            objects because they are not serializable. Including the GeoJSON will result
            in a much larger JSON file.
        """
        data = json_graph.adjacency_data(self)

        if include_geometries_as_geojson:
            convert_geometries_to_geojson(data)
        else:
            remove_geometries(data)

        with open(json_file, "w") as f:
            json.dump(data, f)

    @classmethod
    def from_file(cls, filename, adjacency="rook", cols_to_add=None, reproject=True):
        """Create a :class:`Graph` from a shapefile (or GeoPackage, or GeoJSON, or
        any other library that :mod:`geopandas` can read. See :meth:`from_geodataframe`
        for more details.

        :param cols_to_add: (optional) The names of the columns that you want to
            add to the graph as node attributes. By default, all columns are added.
        """
        df = gp.read_file(filename)
        graph = cls.from_geodataframe(df, adjacency, reproject)
        graph.add_data(df, columns=cols_to_add)
        return graph

    @classmethod
    def from_geodataframe(cls, dataframe, adjacency="rook", reproject=True):
        """Creates the adjacency :class:`Graph` of geometries described by `dataframe`.
        The areas of the polygons are included as node attributes (with key `area`).
        The shared perimeter of neighboring polygons are included as edge attributes
        (with key `shared_perim`).
        Nodes corresponding to polygons on the boundary of the union of all the geometries
        (e.g., the state, if your dataframe describes VTDs) have a `boundary_node` attribute
        (set to `True`) and a `boundary_perim` attribute with the length of this "exterior"
        boundary.

        By default, areas and lengths are computed in a UTM projection suitable for the
        geometries. This prevents the bizarro area and perimeter values that show up when
        you accidentally do computations in Longitude-Latitude coordinates. If the user
        specifies `reproject=False`, then the areas and lengths will be computed in the
        GeoDataFrame's current coordinate reference system. This option is for users who
        have a preferred CRS they would like to use.

        :param dataframe: :class:`geopandas.GeoDataFrame`
        :param adjacency: (optional) The adjacency type to use ("rook" or "queen").
            Default is "rook".
        :return: The adjacency graph of the geometries from `dataframe`.
        :rtype: :class:`Graph`
        """
        # Project the dataframe to an appropriate UTM projection unless
        # explicitly told not to.
        if reproject:
            df = reprojected(dataframe)
        else:
            df = dataframe

        # Generate dict of dicts of dicts with shared perimeters according
        # to the requested adjacency rule
        adjacencies = neighbors(df, adjacency)
        graph = cls(adjacencies)

        graph.issue_warnings()

        # Add "exterior" perimeters to the boundary nodes
        add_boundary_perimeters(graph, df.geometry)

        # Add area data to the nodes
        areas = df.geometry.area.to_dict()
        networkx.set_node_attributes(graph, name="area", values=areas)

        return graph

    def add_data(self, df, columns=None):
        """Add columns of a DataFrame to a graph as node attributes using
        by matching the DataFrame's index to node ids.

        :param df: Dataframe containing given columns.
        :param columns: (optional) List of dataframe column names to add.
        """

        if columns is None:
            columns = df.columns

        check_dataframe(df[columns])

        column_dictionaries = df.to_dict("index")
        networkx.set_node_attributes(self, column_dictionaries)

    def node_attribute(self, node_attribute_key):
        """Create a dictionary of the form ``{node: <attribute value>}`` for
        the given attribute key, over all nodes of the graph.

        This is useful for creating an assignment dictionary from an attribute
        from a source data file. For example, if you created your graph from Census data
        and each node has a `CD` attribute that gives the congressional district
        the node belongs to, then `graph.node_attribute("CD")` would return the
        desired assignment of nodes to CDs.

        :param graph: NetworkX graph.
        :param node_attribute_key: Attribute available on all nodes.
        :return: Dictionary of {node_id: attribute} pairs.
        """
        return {node: data[node_attribute_key] for node, data in self.nodes.items()}

    def join(self, dataframe, columns=None, left_index=None, right_index=None):
        """Add data from a dataframe to the graph, matching nodes to rows when
        the node's `left_index` attribute equals the row's `right_index` value.

        :param dataframe: DataFrame.
        :columns: (optional) The columns whose data you wish to add to the graph.
            If not provided, all columns are added.
        :left_index: (optional) The node attribute used to match nodes to rows.
            If not provided, node IDs are used.
        :right_index: (optional) The DataFrame column name to use to match rows
            to nodes. If not provided, the DataFrame's index is used.
        """
        if right_index is not None:
            df = dataframe.set_index(right_index)
        else:
            df = dataframe

        if columns is not None:
            df = df[columns]

        check_dataframe(df)

        column_dictionaries = df.to_dict()

        if left_index is not None:
            ids_to_index = networkx.get_node_attributes(self, left_index)
        else:
            # When the left_index is node ID, the matching is just
            # a redundant {node: node} dictionary
            ids_to_index = dict(zip(self.nodes, self.nodes))

        node_attributes = {
            node_id: {
                column: values[index] for column, values in column_dictionaries.items()
            }
            for node_id, index in ids_to_index.items()
        }

        networkx.set_node_attributes(self, node_attributes)

    @property
    def islands(self):
        """The set of degree-0 nodes."""
        return set(node for node in self if self.degree[node] == 0)

    def warn_for_islands(self):
        """Issue a warning if the graph has any islands (degree-0 nodes)."""
        islands = self.islands
        if len(self.islands) > 0:
            warnings.warn(
                "Found islands (degree-0 nodes). Indices of islands: {}".format(islands)
            )

    def issue_warnings(self):
        """Issue warnings if the graph has any red flags (right now, only islands)."""
        self.warn_for_islands()


def add_boundary_perimeters(graph, geometries):
    """Add shared perimeter between nodes and the total geometry boundary.

    :param graph: NetworkX graph
    :param df: Geodataframe containing geometry information.
    :return: The updated graph.
    """
    prepared_boundary = prep(unary_union(geometries).boundary)

    boundary_nodes = geometries.boundary.apply(prepared_boundary.intersects)

    for node in graph:
        graph.nodes[node]["boundary_node"] = bool(boundary_nodes[node])
        if boundary_nodes[node]:
            total_perimeter = geometries[node].boundary.length
            shared_perimeter = sum(
                neighbor_data["shared_perim"] for neighbor_data in graph[node].values()
            )
            boundary_perimeter = total_perimeter - shared_perimeter
            graph.nodes[node]["boundary_perim"] = boundary_perimeter


def check_dataframe(df):
    for column in df.columns:
        if sum(df[column].isna()) > 0:
            warnings.warn("NA values found in column {}!".format(column))


def remove_geometries(data):
    """Remove geometry attributes from NetworkX adjacency data object,
    because they are not serializable. Mutates the ``data`` object.

    Does nothing if no geometry attributes are found.

    :param data: an adjacency data object (returned by
        :func:`networkx.readwrite.json_graph.adjacency_data`)
    """
    for node in data["nodes"]:
        bad_keys = []
        for key in node:
            # having a ``__geo_interface__``` property identifies the object
            # as being a ``shapely`` geometry object
            if hasattr(node[key], "__geo_interface__"):
                bad_keys.append(key)
        for key in bad_keys:
            del node[key]


def convert_geometries_to_geojson(data):
    """Convert geometry attributes in a NetworkX adjacency data object
    to GeoJSON, so that they can be serialized. Mutates the ``data`` object.

    Does nothing if no geometry attributes are found.

    :param data: an adjacency data object (returned by
        :func:`networkx.readwrite.json_graph.adjacency_data`)
    """
    for node in data["nodes"]:
        for key in node:
            # having a ``__geo_interface__``` property identifies the object
            # as being a ``shapely`` geometry object
            if hasattr(node[key], "__geo_interface__"):
                # The ``__geo_interface__`` property is essentially GeoJSON.
                # This is what :func:`geopandas.GeoSeries.to_json` uses under
                # the hood.
                node[key] = node[key].__geo_interface__
