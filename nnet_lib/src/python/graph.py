from collections import namedtuple
from graph_pb2 import *
from types_pb2 import *
from global_vars import *
from tensor import *

class Graph:
  def __init__(self, name="DefaultGraph", backend="Reference"):
    assert (backend in backend_alignment)
    self.graph = GraphProto()
    self.graph.name = name
    self.graph.backend = backend
    self.alignment = backend_alignment[backend]

  def __enter__(self):
    if get_graph() != None:
      assert False, "We only support one active graph!"
    set_graph(self)
    return self

  def __exit__(self, *args):
    # At this point, the user has finished building the graph. Before we clear
    # the active graph, we need to remove extraneous reorder operators from the
    # graph.
    self.remove_extra_reorder_ops()
    clear_graph()

  def add_node(self,
               name,
               op,
               input_tensors,
               output_tensor_dims,
               output_tensor_layout=NCHW,
               output_tensor_dtype=Float32,
               output_tensor_dformat=Uncompressed,
               params=None):
    """Create a node and add it to graph.

    Args:
      name: Name of the node.
      op: Operator type.
      input_tensors: A list of input tensors of the node.
      output_tensor_dims: Dimensionality of the output tensor.
      output_tensor_layout: Layout of the output tensor.
      output_tensor_dtype: Data type of the output tensor.
      output_tensor_dformat: Storage format of the output tensor.
      params: The parameters of the node.

    Returns:
      The output tensor of the added node.
    """
    node = self.graph.nodes.add()
    node.name = name
    node.op = op

    # Add the parameter to the node.
    if params != None:
      node.params.CopyFrom(params)

    # Update the node's parents field and append it to the children field of the
    # parents nodes. Also add every input tensor to the node.
    for tensor in input_tensors:
      if tensor.source is not None:
        node.parents.append(tensor.source.name)
        tensor.source.children.append(node.name)
      input_tensor_proto = node.input_tensors.add()
      tensor.to_tensor_proto(input_tensor_proto)

    # Create the output tensor (with the node as its source), and add it to the
    # node.
    output_tensor = Tensor(
        dims=output_tensor_dims,
        name=name,
        data_layout=output_tensor_layout,
        data_type=output_tensor_dtype,
        data_format=output_tensor_dformat,
        source=node,
        alignment=self.alignment)
    output_tensor_proto = node.output_tensors.add()
    output_tensor.to_tensor_proto(output_tensor_proto)

    return output_tensor

  def remove_extra_reorder_ops(self):
    """Remove extraneous reorder operators from the graph.

    After performing automatic layout transformation during the graph creation,
    we may have inserted extraneous reorder operators. For example, an input
    tensor in NCHW is shared by two convolution operators which require input
    in NHWC. Our approach would add two reorder operators for each convolution,
    where only one is needed. This function performs an optimization that
    removes the unnecessary reorder operators and merges them into one.
    """
    nodes_by_name = {}
    # This tuple contains the node and its index into self.graph.nodes. The
    # index will be used for removing nodes from the graph.
    node_index_tuple = namedtuple("node_index_tuple", ["node", "graph_index"])
    for i, node in enumerate(self.graph.nodes):
      nodes_by_name[node.name] = node_index_tuple(node=node, graph_index=i)

    # We keep track of the indices of the nodes that are to be removed.
    to_remove_nodes = set()
    for name in nodes_by_name.iterkeys():
      parent = nodes_by_name[name].node
      target_layouts = []
      reorder_ops = []
      to_remove_children = set()
      for i in range(len(parent.children)):
        child = nodes_by_name[parent.children[i]].node
        graph_index = nodes_by_name[parent.children[i]].graph_index
        if child.op == Reorder:
          layout = child.output_tensors[0].shape.layout
          if layout in target_layouts:
            # This is an extraneous reorder operator.
            index = target_layouts.index(layout)
            merges_into_reorder_op = reorder_ops[index]
            # Mark the reorder op as a child to be removed.
            to_remove_children.add(i)
            # Mark the reorder node to be removed from the graph.
            to_remove_nodes.add(graph_index)
            # For every child of this reorder op, replace its reorder parent
            # with the one that the parent merges into.
            for grandchild_name in child.children:
              grandchild = nodes_by_name[grandchild_name].node
              # This is to preserve the ordering in the parents field. The
              # network builder in C++ relies on the ordering to correctly
              # set the input tensors of operators.
              idx = list(grandchild.parents).index(child.name)
              grandchild.parents[idx] = merges_into_reorder_op.name
              merges_into_reorder_op.children.append(grandchild_name)
          else:
            target_layouts.append(layout)
            reorder_ops.append(child)
      # Remove the children that are marked to be removed. The following only
      # works if the repeated field is a raw type, like string, int32, etc. In
      # our case, the children field type is string.
      if to_remove_children:
        parent.children[:] = [
            child for i, child in enumerate(parent.children)
            if i not in to_remove_children
        ]

    # Remove the nodes that are marked to be removed. We reverse the graph
    # traversal order so that deleting a node won't affect the graph indices of
    # the subsequent nodes to be deleted.
    for index, node in reversed(list(enumerate(self.graph.nodes))):
      if index in to_remove_nodes:
        del self.graph.nodes[index]

  def write_graph(self, name=None):
    """Serialize the graph to a protobuf file.

    Args:
      name: Name of the output protobuf file. If not specified, use the graph's
            name instead.
    """
    if name == None:
      name = self.graph.name + ".pb"
    f = open(name, "w")
    f.write(self.graph.SerializeToString())
    f.close()

  def print_summary(self):
    """Print the summary of the graph.

    This function prints information of all the nodes in the graph, including a
    node's name, operator type, input/output operators and
    input/output tensors.
    """
    print "================================================================="
    print "      Summary of the network: %s (%s)" % (self.graph.name,
                                                     self.graph.backend)
    print "================================================================="
    for node in self.graph.nodes:
      print "Name: %s (%s)" % (node.name, OpType.Name(node.op))
      print "Parents:",
      for i in node.parents:
        print i,
      print "\nChildren:",
      for o in node.children:
        print o,
      print "\nInput tensors:"
      for t in node.input_tensors:
        print " ", t.name, DataType.Name(
            t.data_type), t.shape.dims, DataLayout.Name(
                t.shape.layout), "alignment(%d)" % t.shape.alignment
      print "Output tensors:"
      for t in node.output_tensors:
        print " ", t.name, DataType.Name(
            t.data_type), t.shape.dims, DataLayout.Name(
                t.shape.layout), "alignment(%d)" % t.shape.alignment
      print "-----------------------------------------------------------------"