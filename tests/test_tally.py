from collections import defaultdict

from gerrychain import MarkovChain, Partition
from gerrychain.accept import always_accept
from gerrychain.constraints import no_vanishing_districts, single_flip_contiguous
from gerrychain.grid import Grid
from gerrychain.proposals import propose_random_flip
from gerrychain.random import random
from gerrychain.updaters.tally import DataTally


def random_assignment(graph, num_districts):
    return {node: random.choice(range(num_districts)) for node in graph.nodes}


def test_data_tally_works_as_an_updater(three_by_three_grid):
    assignment = random_assignment(three_by_three_grid, 4)
    data = {node: random.randint(1, 100) for node in three_by_three_grid.nodes}
    parts = tuple(set(assignment.values()))
    updaters = {"tally": DataTally(data, alias="tally")}
    partition = Partition(three_by_three_grid, assignment, updaters)

    flip = {random.choice(list(partition.graph.nodes)): random.choice(parts)}
    new_partition = partition.merge(flip)

    assert new_partition["tally"]


def test_data_tally_gives_expected_value(three_by_three_grid):
    first_node = next(iter(three_by_three_grid.nodes))
    assignment = {node: 1 for node in three_by_three_grid.nodes}
    assignment[first_node] = 2

    data = {node: 1 for node in three_by_three_grid}
    updaters = {"tally": DataTally(data, alias="tally")}
    partition = Partition(three_by_three_grid, assignment, updaters)

    flip = {first_node: 1}
    new_partition = partition.merge(flip)

    assert new_partition["tally"][1] == partition["tally"][1] + 1


def test_data_tally_mimics_old_tally_usage(graph_with_random_data_factory):
    graph = graph_with_random_data_factory(["total"])

    # Make a DataTally the same way you make a Tally
    updaters = {"total": DataTally("total", alias="total")}
    assignment = {i: 1 if i in range(4) else 2 for i in range(9)}

    partition = Partition(graph, assignment, updaters)
    expected_total_in_district_one = sum(graph.nodes[i]["total"] for i in range(4))
    assert partition["total"][1] == expected_total_in_district_one


def test_tally_matches_naive_tally_at_every_step():
    partition = Grid((10, 10), with_diagonals=True)

    chain = MarkovChain(
        propose_random_flip,
        [single_flip_contiguous, no_vanishing_districts],
        always_accept,
        partition,
        1000,
    )

    def get_expected_tally(partition):
        expected = defaultdict(int)
        for node in partition.graph.nodes:
            part = partition.assignment[node]
            expected[part] += partition.graph.nodes[node]["population"]
        return expected

    for state in chain:
        expected = get_expected_tally(state)
        assert expected == state["population"]
