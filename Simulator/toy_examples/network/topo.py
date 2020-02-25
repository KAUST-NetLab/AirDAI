import networkx as nx
import torch.distributed as dist
import numpy as np
import sys
import time
from .logging import logger
import random



##TODO: We should consider about multiple cells, but only one FL server.
# TODO: Therefore, we should use a tag to indicate which computing device
# TODO:  belonging to which cell.


class Topo(nx.DiGraph):
    """
        This is a base class for build network topology and it inherients from
        nx.graph base class, thus has all the features of it.
    """
    def __init__(self, model, backend=None, rank=0, size=1, dist_url=None, *args, **kwargs):
        super(Topo, self).__init__()
        self.model = model
        self.global_initialer()
        self.init_process_group(backend, rank, size, dist_url)


    def global_initialer(self):
        self.start_time = time.time()

    def __str__(self):
        msg = "My rank is %d \n" % self.rank + 'I have nodes on my device: %s \n' % str(self.nodes_on_device)
        for node in self.nodes_on_device:
            msg += "  --name: %s, --attrs: %s \n" % (str(node), self.nodes[node])
            msg += "  --adjcency: %s \n" % (self.adj[node])
        return msg

    def count_parameters_in_MB(self, model):
        return np.sum(np.prod(v.size()) for name, v in model.named_parameters() if "auxiliary" not in name) / 1e6

    def init_process_group(self, backend=None, rank=0, size=1, dist_url=None):
        """
            ##TODO: It is only checked with openmpi, gloo and nccl needs to be implemented
        """
        if dist.is_available():
            if str.lower(backend) == 'mpi':
                if dist.is_mpi_available():
                    dist.init_process_group(backend='mpi')
                    logger.info("System use MPI backend...")
                    return 'mpi'
                else:
                    logger.error("MPI seems not implemented...\n Code will break down...")
                    sys.exit()

            elif str.lower(backend) == 'gloo':
                if dist.is_gloo_available():

                    ### ******************************************************************
                    """
                        This part of code is only temporary. Because Pytorch gloo doesn't 
                        automatically assign a rank to each process. We use mpi4py to get a 
                        rank for it, otherwise, we need assign a rank by hand.
                    """
                    if rank != 0:
                        from mpi4py import MPI
                        comm = MPI.COMM_WORLD
                        rank = comm.Get_rank() + 1
                        size = comm.Get_size() + 1
                    ### ******************************************************************
                    print("world size: ", size, "rank:", rank )
                    dist.init_process_group(backend='gloo', init_method=dist_url,
                            world_size=size, rank=rank)
                    logger.info("System use GLOO backend...")
                    return 'gloo'
                else:
                    logger.error("GLOO seems not implemented...\n Code will break down...")
                    sys.exit()
            return None

    @property
    def is_multiprocess(self):
        """
            It checks whether uses a multiprocess method
        """
        ##TODO: should check whether use multiprocess
        return dist.is_initialized()

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
        logger.info(self)

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
        if dist.is_initialized():
            return dist.get_rank()
        else:
            return 0

    @property
    def time(self):
        return time.time()

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

    @property
    def clients_partitioned(self):
        return list(map(lambda nodes: set(nodes).difference(self.servers), self.partitioned))

    def partition(self, world_size=None):
        """
            Partition the topo clients and servers into a distributed version
            default group is the group.world
        """
        if world_size is None:
            world_size = dist.get_world_size() if self.is_multiprocess else 1
        partitioned = [[] for _ in range(world_size)]

        for i, node in enumerate(self.nodes):
            if self.is_multiprocess:
                if 'rank' not in self.nodes[node].keys():
                    self.nodes[node]['rank'] = i % world_size
                    partitioned[i % world_size] += [node]
                else:
                    partitioned[self.nodes[node]['rank']] += [node]
            else:
                self.nodes[node]['rank'] = 0
                partitioned[0] += [node]
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

    @property
    def seed(self):
        return random.randint(100,100000)

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
    def __init__(self, model, backend=None, rank=0, size=1, dist_url=None, rand_method=('static', 5), *args, **kwargs):
        super(RandTopo, self).__init__(model, backend, rank, size, dist_url, *args, **kwargs)
        self.rand_method = rand_method
        self.load_from_dict(self.load_dict[rand_method[0]])

    @property
    def load_dict(self):
        return {
            'static': self.static_clients(self.rand_method[1])
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

