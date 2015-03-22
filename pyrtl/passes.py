
"""
passes contains structures helpful for writing analysis and
transformation passes over blocks.
"""

import copy
import core
import wire
import helperfuncs
import memory

# --------------------------------------------------------------------
#         __   ___          ___  __  ___              ___    __
#    /\  |__) |__   /\     |__  /__`  |  |  |\/|  /\   |  | /  \ |\ |
#   /~~\ |  \ |___ /~~\    |___ .__/  |  |  |  | /~~\  |  | \__/ | \|
#


def area_estimation(tech_in_nm, block=None):
    """ Returns a single number estimating the total area of the block.

    The tech_in_nm is the size of the circuit technology to be estimated,
    with 65 being 65nm and 250 being 0.25um for example.  The area returned
    is in the units of square mm.
    """
    def mem_area_estimate(tech_in_nm, bits, ports):
        # http://www.cs.ucsb.edu/~sherwood/pubs/ICCD-srammodel.pdf
        tech_in_um = tech_in_nm * 1000
        return 0.001 * tech_in_um**2.07 * bits**0.9 * ports**0.7 + 0.0048

    def gatecount_estimate(net):
        if net.op in 'w~sc':
            return 0
        elif net.op in '&|':
            return 3 * len(net.arg[0])
        elif net.op in '^=<>x':
            return 18 * len(net.arg[0])
        elif net.op == 'r':
            return 20 * len(net.arg[0])
        elif net.op in '+-':
            return 21 * len(net.arg[0])
        elif net.op == '*':
            return 350 * len(net.arg[0])
        elif net.op in 'm@':
            return 0  # memories handled elsewhere
        else:
            raise core.PyrtlInternalError('Unable to estimate the following net '
                                          'due to unimplemented op :\n%s' % str(net))

    block_in = core.working_block(block)

    # first, sum up the area of all of the logic elements (including registers)
    num_gates = sum(gatecount_estimate(a_net) for a_net in block.logic)

    # 854 Kgate/mm2 -- http://www.tsmc.com/english/dedicatedFoundry/technology/65nm.htm
    area_in_65nm = num_gates / 854000.0
    # scaling down from 65nm
    sum_area = area_in_65nm / (65.0/tech_in_nm)**2

    # now sum up the area of the memories
    for mem in set(net.op_param[1] for net in block.logic_subset('@m')):
        bits = 2**mem.addrwidth * mem.bitwidth
        read_ports = len(mem.readport_nets)
        write_ports = len(mem.writeport_nets)
        ports = max(read_ports, write_ports)
        sum_area += mem_area_estimate(tech_in_nm, bits, ports)

    return sum_area


# --------------------------------------------------------------------
#   ___                 __        /\                     __      __
#    |  |  |\/| | |\ | /  `      /~~\ |\ |  /\  |  \_/  /__` |  /__`
#    |  |  |  | | | \| \__>     /    \| \| /~~\ |_  |   .__/ |  .__/
#

def timing_analysis(block=None, gate_delay_funcs=None,):
    """ Calculates the timing analysis while allowing for
    different timing delays of different gates of each type
    Supports all valid presynthesis blocks
    Currently doesn't support memory post synthesis

    :param block: The block you want to do timing analysis on. The default is
     the current working block.
    :param gate_delay_funcs: a map with keys corresponding to the gate op and
     a function returning the delay as the value
     It takes the gate as an argument.
     If the delay is negative (-1), the gate will be treated as the end
     of the block
    :return: returns a map consisting of each wirevector and the associated
     delay
    """
    import heapq

    class WireWTiming:
        # the purpose of this class is to allow for us to define
        # the ordering of the objects. This is needed for the
        # heap to function.

        def __init__(self, timing, wirevector):
            self.time = timing
            self.wirevector = wirevector

        def __lt__(self, other):
            return self.time < other.time

    block = core.working_block(block)
    if gate_delay_funcs is None:
        gate_delay_funcs = {
            '~': lambda gate: 1,
            '&': lambda gate: 1,
            '|': lambda gate: 1,
            '^': lambda gate: 1,
            'n': lambda gate: 1,
            'w': lambda gate: 0,
            '+': lambda gate: len(gate.args[0])*3,
            '-': lambda gate: len(gate.args[0])*3,
            '*': lambda gate: len(gate.args[0])*5,
            '<': lambda gate: len(gate.args[0])*2,
            '>': lambda gate: len(gate.args[0])*2,
            '=': lambda gate: len(gate.args[0])*2,
            'x': lambda gate: 3,
            'c': lambda gate: 0,
            's': lambda gate: 0,
            'r': lambda gate: -1,
            'm': lambda gate: 100,
            '@': lambda gate: -1,
        }

    cleared = block.wirevector_subset((wire.Input, wire.Const, wire.Register))
    remaining = block.logic.copy()
    timing_map = {wirevector: 0 for wirevector in cleared}
    timing_heap = [WireWTiming(0, a_wire) for a_wire in cleared]
    heapq.heapify(timing_heap)
    while len(timing_heap) > 0:
        cleared.add(heapq.heappop(timing_heap).wirevector)
        items_to_remove = set()
        for _gate in remaining:  # loop over logicnets not yet returned
            if cleared.issuperset(_gate.args):  # if all args ready
                gate_delay = gate_delay_funcs[_gate.op](_gate)
                if gate_delay < 0:
                    items_to_remove.add(_gate)
                    continue
                time = max(timing_map[a_wire] for a_wire in _gate.args) + gate_delay
                for dest_wire in _gate.dests:
                    timing_map[dest_wire] = time
                    heapq.heappush(timing_heap, WireWTiming(time, dest_wire))
                cleared.update(set(_gate.dests))  # add dests to set of ready wires
                items_to_remove.add(_gate)
        remaining.difference_update(items_to_remove)

    if len(remaining) > 0:
        block_str = ""
        for a_net in remaining:
            block_str = block_str + str(a_net) + "\n"
        block_str = ("Cannot do static timing analysis due to nonregister,"
                     "nonmemory loops in the code \n"
                     "The unprocesssed blocks are: \n") + block_str
        raise core.PyrtlError(block_str)
    return timing_map


def timing_max_length(timing_map):
    return max(timing_map.itervalues())


def print_max_length(timing_map):
    print "The total block timing delay is ", timing_max_length(timing_map)


def timing_critical_path(timing_map, block=None):
    """
    Takes a timing map and returns the critical paths of the system
    :param timing_map: a timing map from the timing analysis
    :return: a list containing tuples with the 'first' wire as the
    first value and the critical paths (which themselves are lists
    of nets) as the second
    """

    block = core.working_block(block)
    critical_paths = []  # storage of all completed critical paths

    def critical_path_pass(old_critical_path, first_wire):
        if isinstance(first_wire, (wire.Input, wire.Const, wire.Register)):
            critical_paths.append((first_wire, old_critical_path))
            return

        source_list = [anet for anet in block.logic if any(
            (destWire is first_wire) for destWire in anet.dests)]

        if len(source_list) is not 1:
            raise core.PyrtlInternalError("The following net has the wrong number of sources:"
                                          + str(first_wire) + ". It has " + str(len(source_list)))
        source = source_list[0]
        critical_path = source_list
        critical_path.extend(old_critical_path)
        arg_max_time = max(timing_map[arg_wire] for arg_wire in source.args)
        for arg_wire in source.args:
            # if the time for both items are the max, both will be on a critical path
            if timing_map[arg_wire] == arg_max_time:
                critical_path_pass(critical_path, arg_wire)

    max_time = timing_max_length(timing_map)
    for wire_pair in timing_map.viewitems():
        if wire_pair[1] == max_time:
            critical_path_pass([], wire_pair[0])

    line_indent = "  "
    #  print the critical path
    for cp_with_num in enumerate(critical_paths):
        print "Critical path", cp_with_num[0], ":"
        print line_indent, "The first wire is:", cp_with_num[1][0]
        for block in cp_with_num[1][1]:
            print line_indent, (block)
        print

    return critical_paths


def print_analysis(block, wirevector_timing_map, ):
    raise NotImplementedError

# --------------------------------------------------------------------
#   __   __  ___           __      ___    __
#  /  \ |__)  |  |  |\/| |  /  /\   |  | /  \ |\ |
#  \__/ |     |  |  |  | | /_ /~~\  |  | \__/ | \|
#


def optimize(update_working_block=True, block=None):
    """ Return an optimized version of a synthesized hardware block. """
    block = core.working_block(block)
    if not update_working_block:
        block = copy.deepcopy(block)

    if core.debug_mode:
        block.sanity_check()
        _remove_wire_nets(block)
        block.sanity_check()
        _constant_propagation(block)
        block.sanity_check()
        _remove_unlistened_nets(block)
    else:
        _remove_wire_nets(block)
        _constant_propagation(block)
        _remove_unlistened_nets(block)
    return block


def _remove_wire_nets(block):
    """ Remove all wire nodes from the block. """

    def find_producer(x):
        # trace back to the root producer of x
        if x in immediate_producer:
            return find_producer(immediate_producer[x])
        else:
            return x

    immediate_producer = {}  # map from wirevector to its direct producer wirevector
    wire_removal_set = set()  # set of all wirevectors to be removed

    # one pass to build the map of value producers and
    # all of the nets and wires to be removed
    for net in block.logic:
        if net.op == 'w':
            immediate_producer[net.dests[0]] = net.args[0]
            if not isinstance(net.dests[0], wire.Output):
                wire_removal_set.add(net.dests[0])

    # second full pass to create the new logic without the wire nets
    new_logic = set()
    for net in block.logic:
        if net.op != 'w' or isinstance(net.dests[0], wire.Output):
            new_args = tuple(find_producer(x) for x in net.args)
            new_net = core.LogicNet(net.op, net.op_param, new_args, net.dests)
            new_logic.add(new_net)

    # now update the block with the new logic and remove wirevectors
    block.logic = new_logic
    for dead_wirevector in wire_removal_set:
        del block.wirevector_by_name[dead_wirevector.name]
        block.wirevector_set.remove(dead_wirevector)

    block.sanity_check()


def _constant_propagation(block):
    """
    Removes excess constants in the block

    Note on resulting block:
    The output of the block can have wirevectors that are driven but not
    listened to. This is to be expected. These are to be removed by the
    _remove_unlistened_nets function
    """

    current_nets = 0
    while len(block.logic) != current_nets:
        current_nets = len(block.logic)
        _constant_prop_pass(block)


def _constant_prop_pass(block):
    """ Does one constant propagation pass """

    def constant_prop_check(net_checking):

        def replace_net(new_net):
            nets_to_remove.add(net_checking)
            nets_to_add.add(new_net)
            # for arg_wire in net_checking.args:
            #     if arg_wire not in new_net.args:
            #         wire_removal_set.add(arg_wire)

        def replace_net_with_const(const_val):
            new_const_wire = wire.Const(bitwidth=1, val=const_val, block=block)
            wire_add_set.add(new_const_wire)
            replace_net_with_wire(new_const_wire)

        def replace_net_with_wire(new_wire):
            if isinstance(net_checking.dests[0], wire.Output):
                # if isinstance(new_wire,wire.Input) or isinstance(new_wire,wire.Const):
                replace_net(core.LogicNet('w', None, args=(new_wire,),
                                          dests=net_checking.dests))
            else:
                nets_to_remove.add(net_checking)
                replacement_wires[net_checking.dests[0]] = new_wire
                # wire_removal_set.add(net_checking.dests)

        one_var_ops = {
            '~': lambda x: 1-x,
            'r': lambda x: x   # This is only valid for constant folding purposes
        }
        two_var_ops = {
            '&': lambda l, r: l & r,
            '|': lambda l, r: l | r,
            '^': lambda l, r: l ^ r,
            'n': lambda l, r: 1-(l & r),
        }
        num_constants = 0
        for arg_wires in net_checking.args:
            if isinstance(arg_wires, wire.Const):
                num_constants += 1

        if num_constants is 0 or net_checking.op == 'w':
            return None

        if (net_checking.op in two_var_ops) & num_constants is 1:
            # special case
            arg1, arg2 = net_checking.args
            if isinstance(arg1, wire.Const):
                const_wire = arg1
                other_wire = arg2
            else:
                const_wire = arg2
                other_wire = arg1

            outputs = [two_var_ops[net_checking.op](const_wire.val, other_val)
                       for other_val in range(0, 2)]

            if outputs[0] == outputs[1]:
                replace_net_with_const(outputs[0])
            elif outputs[0] == 0:
                replace_net_with_wire(other_wire)
            else:
                replace_net(core.LogicNet('~', None, args=(other_wire,),
                                          dests=net_checking.dests))

        else:
            if net_checking.op in two_var_ops:
                output = two_var_ops[net_checking.op](net_checking.args[0].val,
                                                      net_checking.args[1].val)
            elif net_checking.op in one_var_ops:
                output = one_var_ops[net_checking.op](net_checking.args[0].val)
            else:
                # this is for nets that we are not modifying (eg spliting, and memory)
                return
            replace_net_with_const(output)

    def find_producer(x):
        # trace back to the root producer of x
        if x in replacement_wires:
            return find_producer(replacement_wires[x])
        else:
            return x

    replacement_wires = {}  # map from wire to its producer
    wire_add_set = set()
    nets_to_add = set()
    nets_to_remove = set()

    for a_net in block.logic:
        constant_prop_check(a_net)
    # second full pass to cleanup

    new_logic = set()
    for net in block.logic:
        if net not in nets_to_remove:
            new_args = tuple(find_producer(x) for x in net.args)
            new_net = core.LogicNet(net.op, net.op_param, new_args, net.dests)
            new_logic.add(new_net)
    # now update the block with the new logic and remove wirevectors

    new_logic = new_logic.union(nets_to_add)
    block.logic = new_logic
    for new_wirevector in wire_add_set:
        block.add_wirevector(new_wirevector)

    _remove_unused_wires(block, "constant folding")


def _remove_unlistened_nets(block):
    """
    Removes all nets that are not connected to an output wirevector
    """

    listened_nets = set()
    listened_wires_cur = set()
    prev_listened_net_count = 0

    for a_net in block.logic:
        if a_net.op in 'm@':
            listened_nets.add(a_net)
            for arg_wire in a_net.args:
                listened_wires_cur.add(arg_wire)
        elif isinstance(a_net.dests[0], wire.Output):
            listened_nets.add(a_net)
            for arg_wire in a_net.args:
                listened_wires_cur.add(arg_wire)

    while len(listened_nets) > prev_listened_net_count:
        prev_listened_net_count = len(listened_nets)
        listened_wires_prev = listened_wires_cur

        for net in block.logic:
            if net not in listened_nets:
                if any((destWire in listened_wires_prev) for destWire in net.dests):
                    listened_nets.add(net)
                    for arg_wire in net.args:
                        listened_wires_cur.add(arg_wire)

    # now I need to add back the interface for the inputs that were removed
    for net in block.logic:
        if net.op is 's' and isinstance(net.args[0], wire.Input) and net not in listened_nets:
            listened_nets.add(net)
            # notify the user that this net is useless

    block.logic = listened_nets
    _remove_unused_wires(block, "unlistened net removal")


def _remove_unused_wires(block, parent_process_name):
    """ Removes all unconnected wires from a block"""
    all_wire_vectors = set()
    for logic_net in block.logic:
        for arg_wire in logic_net.args:
            all_wire_vectors.add(arg_wire)
        for dest_wire in logic_net.dests:
            all_wire_vectors.add(dest_wire)

    wire_removal_set = block.wirevector_set.difference(all_wire_vectors)
    for removed_wire in wire_removal_set:
        if isinstance(removed_wire, wire.Input):
            print "Input Wire, " + removed_wire.name + " was removed by " + parent_process_name
        if isinstance(removed_wire, wire.Output):
            core.PyrtlInternalError("Output wire, " + removed_wire.name +
                                    "was disconnected by" + parent_process_name)

    block.wirevector_set = all_wire_vectors

# --------------------------------------------------------------------
#    __           ___       ___  __     __
#   /__` \ / |\ |  |  |__| |__  /__` | /__`
#   .__/  |  | \|  |  |  | |___ .__/ | .__/
#


def synthesize(update_working_block=True, block=None):
    """ Lower the design to just single-bit "and", "or", and "not" gates.

    Takes as input a block (default to working block) and creates a new
    block which is identical in function but uses only single bit gates
    and excludes many of the more complicated primitives.  The new block
    should consist *almost* exclusively of the combination elements
    of w, &, |, ^, and ~ and sequential elements of registers (which are
    one bit as well).  The two exceptions are for inputs/outputs (so that
    we can keep the same interface) which are immediately broken down into
    the individual bits and memories.  Memories (read and write ports) which
    require the reassembly and disassembly of the wirevectors immediately
    before and after.  There are the only two places where 'c' and 's' ops
    should exist.

    The block that results from synthesis is actually of type
    "PostSynthesisBlock" which contains a mapping from the original inputs
    and outputs to the inputs and outputs of this block.  This is used during
    simulation to map the input/outputs so that the same testbench can be
    used both pre and post synthesis (see documentation for Simulation for
    more details).

    :param updated_working_block: Boolean specifying if working block update
    :param block: The block you want to synthesize
    :return: The newly synthesized block (of type PostSynthesisBlock).
    """

    block_in = core.working_block(block)
    block_out = core.PostSynthBlock()
    # resulting block should only have one of a restricted set of net ops
    block_out.legal_ops = set('~&|^nrwcsm@')
    wirevector_map = {}  # map from (vector,index) -> new_wire
    io_map = block_out.io_map  # map from presynth inputs and outputs to postsynth i/o
    uid = 0  # used for unique names

    # First step, create all of the new wires for the new block
    # from the original wires and store them in the wirevector_map
    # for reference.
    for wirevector in block_in.wirevector_subset():
        for i in range(len(wirevector)):
            new_name = '_'.join(['synth', wirevector.name, str(i), str(uid)])
            uid += 1
            if isinstance(wirevector, wire.Const):
                new_val = (wirevector.val >> i) & 0x1
                new_wirevector = wire.Const(bitwidth=1, val=new_val, block=block_out)
            elif isinstance(wirevector, (wire.Input, wire.Output)):
                new_wirevector = wire.WireVector(name=new_name, bitwidth=1, block=block_out)
            else:
                new_wirevector = wirevector.__class__(name=new_name, bitwidth=1, block=block_out)
            wirevector_map[(wirevector, i)] = new_wirevector

    # Now connect up the inputs and outputs to maintain the interface
    for wirevector in block_in.wirevector_subset(wire.Input):
        input_vector = wire.Input(name=wirevector.name, bitwidth=len(wirevector), block=block_out)
        io_map[wirevector] = input_vector
        for i in range(len(wirevector)):
            wirevector_map[(wirevector, i)] <<= input_vector[i]
    for wirevector in block_in.wirevector_subset(wire.Output):
        output_vector = wire.Output(name=wirevector.name, bitwidth=len(wirevector), block=block_out)
        io_map[wirevector] = output_vector
        # the "reversed" is needed because most significant bit comes first in concat
        output_bits = [wirevector_map[(wirevector, i)] for i in reversed(range(len(output_vector)))]
        output_vector <<= helperfuncs.concat(*output_bits)

    # Now that we have all the wires built and mapped, walk all the blocks
    # and map the logic to the equivalent set of primitives in the system
    out_mems = block_out.mem_map  # dictionary: PreSynth Map -> PostSynth Map
    for net in block_in.logic:
        _decompose(net, wirevector_map, out_mems, block_out)

    if update_working_block:
        core.set_working_block(block_out)
    return block_out


def _decompose(net, wv_map, mems, block_out):
    """ Add the wires and logicnets to block_out and wv_map to decompose net """

    def arg(x, i):
        # return the mapped wire vector for argument x, wire number i
        return wv_map[(net.args[x], i)]

    def destlen():
        # return the length of the destination in bits
        return range(len(net.dests[0]))

    def assign_dest(i, v):
        # assign v to the wiremap for dest[0], wire i
        wv_map[(net.dests[0], i)] <<= v

    if net.op == 'w':
        for i in destlen():
            assign_dest(i, arg(0, i))
    elif net.op == '~':
        for i in destlen():
            assign_dest(i, ~arg(0, i))
    elif net.op == '&':
        for i in destlen():
            assign_dest(i, arg(0, i) & arg(1, i))
    elif net.op == '|':
        for i in destlen():
            assign_dest(i, arg(0, i) | arg(1, i))
    elif net.op == '^':
        for i in destlen():
            assign_dest(i, arg(0, i) ^ arg(1, i))
    elif net.op == 'n':
        for i in destlen():
            assign_dest(i, arg(0, i).nand(arg(1, i)))
    elif net.op == '=':
        # The == operator is implemented with a nor of xors.
        temp_result = arg(0, 0) ^ arg(1, 0)
        for i in range(1, len(net.args[0])):
            temp_result = temp_result | (arg(0, i) ^ arg(1, i))
        assign_dest(0, ~temp_result)
    elif net.op == 'x':
        for i in destlen():
            muxed_bit = ~arg(0, 0) & arg(1, i) | arg(0, 0) & arg(2, i)
            assign_dest(i, muxed_bit)
    elif net.op == 's':
        for i in destlen():
            selected_bit = arg(0, net.op_param[i])
            assign_dest(i, selected_bit)
    elif net.op == 'c':
        arg_wirelist = []
        # generate list of wires for vectors being concatenated
        for arg_vector in net.args:
            arg_vector_as_list = [wv_map[(arg_vector, i)] for i in range(len(arg_vector))]
            arg_wirelist = arg_vector_as_list + arg_wirelist
        for i in destlen():
            assign_dest(i, arg_wirelist[i])
    elif net.op == 'r':
        for i in destlen():
            args = (arg(0, i),)
            dests = (wv_map[(net.dests[0], i)],)
            new_net = core.LogicNet('r', None, args=args, dests=dests)
            block_out.add_net(new_net)
    elif net.op == '+':
        arg0list = [arg(0, i) for i in range(len(net.args[0]))]
        arg1list = [arg(1, i) for i in range(len(net.args[1]))]
        cin = wire.Const(0, bitwidth=1, block=block_out)
        sumbits, cout = _generate_add(arg0list, arg1list, cin)
        destlist = sumbits + [cout]
        for i in destlen():
            assign_dest(i, destlist[i])
    elif net.op == '>':
        # where xi = Ai==Bi then
        # A>B = A3 & ~B3 | A2 & ~B2 & x3 | A1 & ~B1 & x3 & x2 | A0 & ~B0 & x3 & x2 & x1
        bitlen = len(net.args[0])
        # Compute the xi above, but don't compute x0 (put None in it's place)
        x = [~(arg(0, i) ^ arg(1, i)) for i in range(1, bitlen)]
        x.insert(0, None)
        # OR over all the terms
        result = None
        for i in range(0, bitlen):
            term = arg(0, i) & ~arg(1, i)
            for j in range(i+1, bitlen):
                term = term & x[j]
            result = (term) if result is None else (result | term)
        assign_dest(0, result)
    elif net.op == '<':
        # where xi = Ai==Bi then
        # A<B = ~A3 & B3 | ~A2 & B2 & x3 | ~A1 & B1 & x3 & x2 | ~A0 & B0 & x3 & x2 & x1
        bitlen = len(net.args[0])
        # Compute the xi above, but don't compute x0 (put None in it's place)
        x = [~(arg(0, i) ^ arg(1, i)) for i in range(1, bitlen)]
        x.insert(0, None)
        # OR over all the terms
        result = None
        for i in range(0, bitlen):
            term = ~arg(0, i) & arg(1, i)
            for j in range(i+1, bitlen):
                term = term & x[j]
            result = (term) if result is None else (result | term)
        assign_dest(0, result)
    elif net.op == '-':
        arg0list = [arg(0, i) for i in range(len(net.args[0]))]
        arg1list = [~arg(1, i) for i in range(len(net.args[1]))]
        cin = wire.Const(1, bitwidth=1, block=block_out)
        sumbits, cout = _generate_add(arg0list, arg1list, cin)
        destlist = sumbits + [cout]
        for i in destlen():
            assign_dest(i, destlist[i])
    elif net.op == 'm':
        arg0list = [arg(0, i) for i in range(len(net.args[0]))]
        addr = helperfuncs.concat(*reversed(arg0list))
        memid, mem = net.op_param
        if mem not in mems:
            new_mem = mem._make_copy(block_out)
            mems[mem] = new_mem
            new_mem.id = mem.id
        else:
            new_mem = mems[mem]
        data = helperfuncs.as_wires(new_mem[addr])
        for i in destlen():
            assign_dest(i, data[i])
    elif net.op == '@':
        addrlist = [arg(0, i) for i in range(len(net.args[0]))]
        addr = helperfuncs.concat(*reversed(addrlist))
        datalist = [arg(1, i) for i in range(len(net.args[1]))]
        data = helperfuncs.concat(*reversed(datalist))
        enable = arg(2, 0)
        memid, mem = net.op_param
        if mem not in mems:
            new_mem = mem._make_copy(block_out)
            mems[mem] = new_mem
            new_mem.id = mem.id
        else:
            new_mem = mems[mem]
        new_mem[addr] <<= memory.MemBlock.EnabledWrite(data=data, enable=enable)
    else:
        raise core.PyrtlInternalError('Unable to synthesize the following net '
                                      'due to unimplemented op :\n%s' % str(net))
    return


def _synth_base(block):
    """
    This is a generic function to copy the wirevectors for another round of
    synthesis This does not split a wirevector with multiple wires.
    :param block: The block to change
    :return: the resulting block and a wirevector map
    """
    import copy
    block_in = core.working_block(block)
    if not isinstance(block_in, core.PostSynthBlock):
        raise core.PyrtlError("Synth_base only works on post synth blocks")
    block_out = core.PostSynthBlock()
    temp_wv_map = {}
    temp_io_map = {}
    for wirevector in block_in.wirevector_subset():
        new_wv = copy.copy(wirevector)
        new_wv.block = block_out
        new_wv.name = core.next_tempvar_name()
        temp_wv_map[wirevector] = new_wv
        if isinstance(wirevector, (wire.Input, wire.Output)):
            temp_io_map[wirevector] = new_wv

    block_out.io_map = {orig_wire: temp_io_map[v] for (orig_wire, v) in block_in.io_map}
    # TODO: figure out the real map
    return block_out, temp_wv_map

def _copy_net(block_out, net, temp_wv_net, mems):
    """This function makes a copy of all nets passed to it for synth uses
    """
    import copy
    if net.op in "~&|^nrwcsm@":
        new_args = (temp_wv_net[a_arg] for a_arg in net.args)
        new_dests = (temp_wv_net[a_dest] for a_dest in net.dest)
        new_param = copy.copy(net.op_param)
        # special stuff for copying memories
        if net.op in "m@":
            memid, mem = net.op_param
            if mem not in mems:
                new_mem = mem._make_copy(block_out)
                mems[mem] = new_mem
                new_mem.id = mem.id
            else:
                new_mem = mems[mem]
            new_param = (new_mem.id, new_mem)

        new_net = core.LogicNet(net.op, new_param, args=new_args, dests=new_dests)
        block_out.add_net(new_net)
    else:
        raise core.PyrtlInternalError("Invalid op code :" + net.op + " found.")


def nand_synth(block=core.working_block()):
    """
    Synthesizes an and-inverter block into one consisting of nands and inverters
    :param block: The block to synthesize.
    :return: The resulting block
    """
    block_in = core.working_block(block)
    block_out, temp_wv_map = _synth_base(block)
    block_out.legal_ops = set('~norwcsm@')
    mems = {}

    def arg(arg_w):
        return temp_wv_map[net.args[arg_w]]

    def assign_dest(toAssign):
        temp_wv_map[net.dests[0]] <<= toAssign

    for net in block_in:
        if net.op == '&':
            assign_dest(~(arg(0).nand(arg(1))))
        elif net.op == '|':
            assign_dest((~arg(0)).nand(~arg(1)))
        elif net.op == '^':
            temp_0 = arg(0).nand(arg(1))
            assign_dest(temp_0.nand(arg(0)).nand(temp_0.nand(arg(1))))
        else:
            _copy_net(block_out, net, temp_wv_map, mems)


def and_inverter_synth(block=core.working_block()):
    """
    Synthesizes a decomposed block into one consisting of ands and inverters
    :param block: The block to synthesize
    :return: The resulting block
    """
    block_in = core.working_block(block)
    block_out, temp_wv_map = _synth_base(block)
    block_out.legal_ops = set('~&rwcsm@')
    mems = {}

    def arg(arg_w):
        return temp_wv_map[net.args[arg_w]]

    def assign_dest(to_assign):
        temp_wv_map[net.dests[0]] <<= to_assign

    for net in block_in:
        if net.op == '|':
            assign_dest(~(~arg(0) & ~arg(1)))
        elif net.op == '^':
            all_1 = arg(0) & arg(1)
            all_0 = ~arg(0) & ~arg(1)
            assign_dest(all_0 & ~all_1)
        elif net.op == 'n':
            assign_dest(~(arg(0) & arg(1)))
        else:
            _copy_net(block_out, net, temp_wv_map, mems)


def _generate_one_bit_add(a, b, cin):
    """ Generates hardware for a 1-bit full adder.
        Input: 3 1-bit wire vectors
        Output: a list of wire vectors (the sum), and a single 1-bit wirevector cout
    """
    sum = a ^ b ^ cin
    cout = a & b | a & cin | b & cin
    return [sum], cout


def _generate_add(a, b, cin):
    """ a and b are lists of wirevectors (all len 1)
        cin is a wirevector (also len 1)
        returns sum as list of wirevectors (all len 1)
        and a carry out wirevector (also len 1)
    """
    if len(a) == 1:
        sumbits, cout = _generate_one_bit_add(a[0], b[0], cin)
    else:
        lsbit, ripplecarry = _generate_one_bit_add(a[0], b[0], cin)
        msbits, cout = _generate_add(a[1:], b[1:], ripplecarry)
        sumbits = lsbit + msbits  # append to lsb to the lowest bits
    return sumbits, cout
