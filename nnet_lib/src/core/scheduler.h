#include <string>
#include <vector>

#include "core/backend.h"
#include "core/operator.h"
#include "core/network.h"
#include "core/tensor.h"
#include "core/workspace.h"
#include "utility/debug_stream.h"

namespace smaug {

template <typename Backend>
Tensor<Backend>* runNetwork(Network* network, Workspace* workspace) {
    const Graph& graph = network->getGraph();
    std::list<Vertex> vertices;
    boost::topological_sort(graph, std::front_inserter(vertices));
    Tensor<Backend>* output;
    for (auto v : vertices) {
        Operator* op = get(boost::vertex_op, graph, v);
        dout(0) << op->getName() << "\n";
        op->run();
        output = op->getOutput<Backend>(0);
        dout(0) << *output << "\n";
    }
    return output;
}

}  // namespace smaug