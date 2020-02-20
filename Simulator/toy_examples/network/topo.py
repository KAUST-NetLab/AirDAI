import networkx as nx
import torch.distributed as dist
import numpy as np

##TODO: We should consider about multiple cells, but only one FL server.
# TODO: Therefore, we should use a tag to indicate which computing device
# TODO:  belonging to which cell.


class Topo(nx.DiGraph):
    """
        This is a base class for build network topology and it inherients from
        nx.graph base class, thus has all the features of it.
    """
    def __init__(self, model, *args, **kwargs):
        super(Topo, self).__init__()
        self.model = model
        dist.init_process_group(backend='mpi')

    def __str__(self):
        msg = 'node: \n'
        for node in self.nodes:
            msg += "  --name: %s, --attrs: %s \n" % (str(node), self.nodes[node])
            msg += "  --adjcency: %s \n" % (self.adj[node])
        return msg

    def count_parameters_in_MB(self, model):
        return np.sum(np.prod(v.size()) for name, v in model.named_parameters() if "auxiliary" not in name) / 1e6

    @property
    def model_size(self):
        return self.count_parameters_in_MB(self.model)

    def load_from_dict(self, dict):
        """
            load a graph from a dict of {'c1': {node_attrs: {}, adj: {'c2': {edge_attrs: }}}}
            The keys() of dict is the nodes,
            the values() of each key is the node attributes and adjacency of the node
        """
        self.add_nodes_from(zip(dict.keys(), dict.values()))

        for node in self.nodes:
            if 'adj' in dict[node].keys():
                adj = dict[node]['adj']
                self.add_edges_from(zip([node]*len(adj), adj.keys(), adj.values()))

        self.partition()
        self.defaults()

    def defaults(self):
        """
            It checks whether some attributes have not registered,
            If so, give them some default values.
        """
        for data, default in self.defaults_dict:
            for k, v in dict(self.nodes(data=data, default=default)).items():
                self.nodes[k][data] = v

    @property
    def defaults_dict(self):
        data = {
            'type': 'client',
            'send_P': 1e-4,
            'recv_P': 1e-4,
            'cal_P': 1e-4,
            'energy': 30,
            'movable': False
        }
        return data.items()

    def in_links(self, node):
        """
            return the input links given a node
        """
        return self.predecessors(node)

    def out_links(self, node):
        """
            return the output links given a node
        """
        return self.successors(node)

    @property
    def in_graph(self):
        """
            return a dict with nodes being the key and its
            in_links being the corresponding values.
        """
        return {node: list(self.in_links(node)) for node in self.nodes}

    @property
    def out_graph(self):
        """
            return a dict with nodes being the key and its
            out_links being the corresponding values.
        """
        return {node: list(self.out_links(node)) for node in self.nodes}

    @property
    def servers(self):
        """
            It checks which node is a FL server and return the FL server nodes
        """

        return [k for k, v in dict(self.nodes(data='type', default='client')).items() if v is not 'client']

    @property
    def clients(self):
        """
            It checks which node is a computing device and return the computing device nodes
        """
        return [k for k, v in dict(self.nodes(data='type', default='client')).items() if v is 'client']

    @property
    def rank(self):
        """
            In a distributed task, it returns the rank id of current computing machine
        """
        return dist.get_rank()

    @property
    def monitor_rank(self):
        """
        :return: During each round of training, the computing machine whose rank is monitor_rank will
                update the effective topology and broadcast the removed nodes and edges to other ranks.
        """
        return 0

    @property
    def partitioned(self):
        """
        :return: A list with the indices being the rank,
                    and the values being the nodes on that rank
        """
        return self.__dict__['partitioned']

    def partition(self, world_size=None):
        """
            Partition the topo clients and servers into a distributed version
            default group is the group.world
        """
        if world_size is None:
            world_size = dist.get_world_size()
        partitioned = [[] for _ in range(world_size)]
        for i, node in enumerate(self.nodes):
            if 'rank' not in self.nodes[node].keys():
                self.nodes[node]['rank'] = i % world_size
                partitioned[i % world_size] += [node]
            else:
                partitioned[self.nodes[node]['rank']] += [node]
        self.__dict__['partitioned'] = partitioned

    def remove_adj_from(self, nodes, edges):
        for node in self.nodes:
            for adj_node in set(self.nodes[node]['adj']).intersection(set(nodes)):
                del self.nodes[node]['adj'][adj_node]

        for edge in edges:
            from_node, to_node = edge
            if from_node in self.nodes:
                if to_node in self.nodes[from_node]['adj']:
                    del self.nodes[from_node]['adj'][to_node]

    def remove(self, nodes, edges):
        """
            :param nodes: required removed nodes
            :param edges: required removed directed edges
            :return: topo object, in which nodes are deleted and adjacent nodes are also deleted.
        """
        nodes = list(nodes)
        edges = list(edges)
        #TODO: Should make it clear
        self.remove_nodes_from(nodes)
        self.remove_edges_from(edges)
        self.remove_adj_from(nodes, edges)
        self.partitioned[self.rank] = list(set(self.partitioned[self.rank]).difference(set(nodes)))

    @property
    def nodes_on_device(self):
        return self.partitioned[self.rank]

    @property
    def clients_on_device(self):
        return [node for node in self.nodes_on_device if self.nodes[node]['type'] == 'client']

    @property
    def servers_on_device(self):
        return [node for node in self.nodes_on_device if self.nodes[node]['type'] == 'server']

    def set_node(self, node, attr):
        """
            Update the node property given the node id and the attr,
            where attr is a dict.
        """
        for k, v in attr.items():
            self.nodes[node][k] = v

    def report(self):
        """
            Make a report about the QoS output.
        """
        ##TODO: should make a standard output report
        print(self.__iter__())


class RandTopo(Topo):
    """
        This is a class inherient from Topo,
    """
    def __init__(self, model, rand_method, *args, **kwargs):
        super(RandTopo, self).__init__(model, *args, **kwargs)
        self.args = args
        self.load_from_dict(self.load_dict[rand_method])

    @property
    def load_dict(self):
        return {
            'static': self.static_clients(self.args[0])
        }

    def static_clients(self, n_clients):
        dict = {'c0':{
                        'type': 'server',
                        'energy': float('inf'),
                        'movable': False,
                        'adj': {}
                        }
                }
        for i in range(n_clients):
            client_name = 'c'+str(i+1)
            dict[client_name] = {
                                    'type': 'client',
                                    'adj': {
                                            'c0': {
                                                    'channel': 'Gaussian'
                                            }
                                    }
                                 }
            dict['c0']['adj'][client_name] = {'channel': 'Gaussian'}

        return dict