# Copyright (c) 2012--2014 King's College London
# Created by the Software Development Team <http://soft-dev.org/>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

from __future__ import print_function

try:
    import cPickle as pickle
except:
    import pickle

import time, os

from grammar_parser.gparser import Parser, Nonterminal, Terminal,MagicTerminal, Epsilon, IndentationTerminal
from syntaxtable import SyntaxTable, FinishSymbol, Reduce, Goto, Accept, Shift
from stategraph import StateGraph
from constants import LR0, LR1, LALR
from astree import AST, TextNode, BOS, EOS

import logging

Node = TextNode

class IncParser(object):

    def __init__(self, grammar=None, lr_type=LR0, whitespaces=False, startsymbol=None):

        if grammar:
            logging.debug("Parsing Grammar")
            parser = Parser(grammar, whitespaces)
            parser.parse()

            filename = "".join([os.path.dirname(__file__), "/../pickle/", str(hash(grammar) ^ hash(whitespaces)), ".pcl"])
            try:
                logging.debug("Try to unpickle former stategraph")
                f = open(filename, "r")
                start = time.time()
                self.graph = pickle.load(f)
                end = time.time()
                logging.debug("unpickling done in %s", end-start)
            except IOError:
                logging.debug("could not unpickle old graph")
                logging.debug("Creating Stategraph")
                self.graph = StateGraph(parser.start_symbol, parser.rules, lr_type)
                logging.debug("Building Stategraph")
                self.graph.build()
                logging.debug("Pickling")
                pickle.dump(self.graph, open(filename, "w"))

            if lr_type == LALR:
                self.graph.convert_lalr()

            logging.debug("Creating Syntaxtable")
            self.syntaxtable = SyntaxTable(lr_type)
            self.syntaxtable.build(self.graph)

        self.stack = []
        self.ast_stack = []
        self.all_changes = []
        self.undo = []
        self.last_shift_state = 0
        self.validating = False
        self.last_status = False
        self.error_node = None
        self.whitespaces = whitespaces
        self.status_by_version = {}
        self.errornode_by_version = {}

        self.previous_version = None
        logging.debug("Incemental parser done")

    def from_dict(self, rules, startsymbol, lr_type, whitespaces, pickle_id, precedences):
        self.graph = None
        self.syntaxtable = None
        if pickle_id:
            filename = "".join([os.path.dirname(__file__), "/../pickle/", str(pickle_id ^ hash(whitespaces)), ".pcl"])
            try:
                f = open(filename, "r")
                self.syntaxtable = pickle.load(f)
            except IOError:
                pass
        if self.syntaxtable is None:
            self.graph = StateGraph(startsymbol, rules, lr_type)
            self.graph.build()
            self.syntaxtable = SyntaxTable(lr_type)
            self.syntaxtable.build(self.graph, precedences)
            if pickle_id:
                pickle.dump(self.syntaxtable, open(filename, "w"))

        self.whitespaces = whitespaces

    def init_ast(self, magic_parent=None):
        bos = BOS(Terminal(""), 0, [])
        eos = EOS(FinishSymbol(), 0, [])
        bos.magic_parent = magic_parent
        eos.magic_parent = magic_parent
        bos.next_term = eos
        eos.prev_term = bos
        root = Node(Nonterminal("Root"), 0, [bos, eos])
        self.previous_version = AST(root)
        root.save(0)
        bos.save(0)
        eos.save(0)

    def reparse(self):
        self.inc_parse([], True)

    def inc_parse(self, line_indents=[], reparse=False):
        logging.debug("============ NEW INCREMENTAL PARSE ================= ")
        self.error_node = None
        self.stack = []
        self.undo = []
        self.current_state = 0
        self.stack.append(Node(FinishSymbol(), 0, []))
        bos = self.previous_version.parent.children[0]
        la = self.pop_lookahead(bos)
        self.loopcount = 0
        self.comment_mode = False

        USE_OPT = True

        while(True):
            self.loopcount += 1
            if self.comment_mode:
                if la.lookup == "cmt_end":
                    # in comment mode we just add all subtrees as they are to a
                    # subtree COMMENT subtrees that have changes are broken
                    # apart, e.g. to be able to find an inserted */ the CMT
                    # subtree is then added to the parsers stack without
                    # changing its state when the parser later reduces stack
                    # elements to a new subtree, CMT subtrees are added as
                    # children
                    next_la = self.pop_lookahead(la)
                    self.comment_mode = False
                    comment_stack.append(la)
                    CMT = Node(Nonterminal("~COMMENT~"))
                    for c in comment_stack:
                        self.undo.append((c, 'parent', c.parent))
                        self.undo.append((c, 'left', c.left))
                        self.undo.append((c, 'right', c.right))
                    CMT.set_children(comment_stack)
                    CMT.state = self.current_state
                    self.stack.append(CMT)
                    la = next_la
                    continue
                if isinstance(la, EOS):
                    self.comment_mode = False
                    self.do_undo(la)
                    self.last_status = False
                    return False
                la = self.add_to_stack(la, comment_stack)
                continue
            if isinstance(la.symbol, Terminal) or isinstance(la.symbol, FinishSymbol) or la.symbol == Epsilon():
                if la.changed:#self.has_changed(la):
                    assert False # with prelexing you should never end up here!
                else:
                    if la.lookup == "cmt_start":
                        # when we find a cmt_start token, we enter comment mode
                        self.comment_mode = True
                        comment_stack = []
                        comment_stack.append(la)
                        # since unchanged subtrees are left untouched, we
                        # wouldn't find a cmt_end if it is part of another
                        # comment, e.g. /* foo /* bar */ to be able to merge
                        # two comment together, we need to find the next
                        # cmt_end and mark its subtree as changed
                        end = la
                        # XXX configure these through the grammar, e.g. Java
                        # needs /*@*/, Python """@""" (@ means, match anything)
                        while True:
                            end = end.next_term
                            if isinstance(end, EOS):
                                break
                            if end.symbol.name.find("*/") > 0:
                                # split token
                                self.lexer.split_endcomment(end)
                                break
                            if end.lookup == "cmt_end":
                                end.mark_changed()
                                break
                        la = self.pop_lookahead(la)
                        continue

                    lookup_symbol = self.get_lookup(la)
                    result = self.parse_terminal(la, lookup_symbol)
                    if result == "Accept":
                        self.last_status = True
                        return True
                    elif result == "Error":
                        self.last_status = False
                        return False
                    elif result != None:
                        la = result

            else: # Nonterminal
                if la.changed or reparse:
                    la.changed = False
                    self.undo.append((la, 'changed', True))
                    la = self.left_breakdown(la)
                else:
                    if USE_OPT:
                        goto = self.syntaxtable.lookup(self.current_state, la.symbol)
                        if goto: # can we shift this Nonterminal in the current state?
                            logging.debug("OPTShift: %s in state %s -> %s", la.symbol, self.current_state, goto)
                            follow_id = goto.action
                            self.stack.append(la)
                            la.state = follow_id #XXX this fixed goto error (i should think about storing the states on the stack instead of inside the elements)
                            self.current_state = follow_id
                            la = self.pop_lookahead(la)
                            self.validating = True
                            continue
                        else:
                            #XXX can be made faster by providing more information in syntax tables
                            first_term = la.find_first_terminal()

                            lookup_symbol = self.get_lookup(first_term)
                            element = self.syntaxtable.lookup(self.current_state, lookup_symbol)
                            if isinstance(element, Reduce):
                                self.reduce(element)
                            else:
                                la = self.left_breakdown(la)
                    else:
                    # PARSER WITHOUT OPTIMISATION
                        if la.lookup != "":
                            lookup_symbol = Terminal(la.lookup)
                        else:
                            lookup_symbol = la.symbol
                        element = self.syntaxtable.lookup(self.current_state, lookup_symbol)

                        if self.shiftable(la):
                            self.stack.append(la)
                            self.current_state = la.state
                            self.right_breakdown()
                            la = self.pop_lookahead(la)
                        else:
                            la = self.left_breakdown(la)
        logging.debug("============ INCREMENTAL PARSE END ================= ")

    def add_to_stack(self, la, stack):
        # comment helper that adds elements to the comment stack and if la is a
        # subtree with changes, recursively break it apart and adds its
        # children

        while True:
            if isinstance(la.symbol, Terminal) and la.lookup == "cmt_end":
                return la
            if isinstance(la, EOS):
                return la
            if la.changed:
                if la.children:
                    la = la.children[0]
                else:
                    la = self.pop_lookahead(la)
                continue
            else:
                stack.append(la)
                la = self.pop_lookahead(la)
                continue

    def parse_terminal(self, la, lookup_symbol):
        element = self.syntaxtable.lookup(self.current_state, lookup_symbol)
        logging.debug("parse_terminal: %s in %s -> %s", lookup_symbol, self.current_state, element)
        if isinstance(element, Accept):
            #XXX change parse so that stack is [bos, startsymbol, eos]
            bos = self.previous_version.parent.children[0]
            eos = self.previous_version.parent.children[-1]
            self.previous_version.parent.set_children([bos, self.stack[1], eos])
            logging.debug("loopcount: %s", self.loopcount)
            logging.debug ("Accept")
            return "Accept"
        elif isinstance(element, Shift):
            logging.debug("Shift: %s -> %s", la, element.action)
            # removing this makes "Valid tokens" correct, should not be needed
            # for incremental parser
            #self.undo.append((la, "state", la.state))
            la.state = element.action
            self.stack.append(la)
            self.current_state = element.action
            if not la.lookup == "<ws>":
                # last_shift_state is used to predict next symbol
                # whitespace destroys correct behaviour
                self.last_shift_state = element.action
            return self.pop_lookahead(la)

        elif isinstance(element, Reduce):
            logging.debug("Reduce: %s -> %s", la, element)
            self.reduce(element)
            return self.parse_terminal(la, lookup_symbol)
        elif element is None:
            if self.validating:
                self.right_breakdown()
                self.validating = False
            else:
                return self.do_undo(la)

    def get_lookup(self, la):
        if la.lookup != "":
            lookup_symbol = Terminal(la.lookup)
        else:
            lookup_symbol = la.symbol
        if isinstance(lookup_symbol, IndentationTerminal):
            #XXX hack: change parsing table to accept IndentationTerminals
            lookup_symbol = Terminal(lookup_symbol.name)
        return lookup_symbol

    def do_undo(self, la):
        while len(self.undo) > 0:
            node, attribute, value = self.undo.pop(-1)
            setattr(node, attribute, value)
        self.error_node = la
        logging.debug ("Error: %s %s %s", la, la.prev_term, la.next_term)
        logging.debug("loopcount: %s", self.loopcount)
        return "Error"

    def reduce(self, element):
        # Reduces elements from the stack to a Nonterminal subtree.  special:
        # COMMENT subtrees that are found on the stack during reduction are
        # added "silently" to the subtree (they don't count to the amount of
        # symbols of the reduction)
        children = []
        i = 0
        while i < element.amount():
            c = self.stack.pop()
            # apply folding information from grammar to tree nodes
            fold = element.action.right[element.amount()-i-1].folding
            c.symbol.folding = fold
            children.insert(0, c)
            if c.symbol.name != "~COMMENT~":
                i += 1
        if self.stack[-1].symbol.name == "~COMMENT~":
            c = self.stack.pop()
            children.insert(0, c)
        logging.debug("   Element on stack: %s(%s)", self.stack[-1].symbol, self.stack[-1].state)
        self.current_state = self.stack[-1].state #XXX don't store on nodes, but on stack

        goto = self.syntaxtable.lookup(self.current_state, element.action.left)
        if goto is None:
            raise Exception("Reduction error on %s in state %s: goto is None" % (element, self.current_state))
        assert goto != None

        # save childrens parents state
        for c in children:
            self.undo.append((c, 'parent', c.parent))
            self.undo.append((c, 'left', c.left))
            self.undo.append((c, 'right', c.right))

        new_node = Node(element.action.left.copy(), goto.action, children)
        logging.debug("   Add %s to stack and goto state %s", new_node.symbol, new_node.state)
        self.stack.append(new_node)
        self.current_state = new_node.state # = goto.action
        if getattr(element.action.annotation, "interpret", None):
            # eco grammar annotations
            self.interpret_annotation(new_node, element.action)
        else:
            # johnstone annotations
            self.add_alternate_version(new_node, element.action)

    def interpret_annotation(self, node, production):
        annotation = production.annotation
        if annotation:
            astnode = annotation.interpret(node)
            node.alternate = astnode

    def add_alternate_version(self, node, production):
        # add alternate (folded) versions for nodes to the tree
        alternate = TextNode(node.symbol.__class__(node.symbol.name), node.state, [])
        alternate.children = []
        teared = []
        for i in range(len(node.children)):
            if production.inserts.has_key(i):
                # insert teared nodes at right position
                value = production.inserts[i]
                for t in teared:
                    if t.symbol.name == value.name:
                        alternate.children.append(t)
            c = node.children[i]
            if c.symbol.folding == "^^^":
                c.symbol.folding = None
                teared.append(c)
                continue
            elif c.symbol.folding == "^^":
                while c.alternate is not None:
                    c = c.alternate
                alternate.symbol = c.symbol
                for child in c.children:
                    alternate.children.append(child)
            elif c.symbol.folding == "^":
                while c.alternate is not None:
                    c = c.alternate
                for child in c.children:
                    alternate.children.append(child)
            else:
                alternate.children.append(c)
        node.alternate = alternate

    def left_breakdown(self, la):
        if len(la.children) > 0:
            return la.children[0]
        else:
            return self.pop_lookahead(la)

    def right_breakdown(self):
        node = self.stack.pop()
        self.current_state = self.stack[-1].state
        logging.debug("right breakdown: set state to %s", self.current_state)
        while(isinstance(node.symbol, Nonterminal)):
            for c in node.children:
                self.shift(c)
            node = self.stack.pop()
            # after undoing an optimistic shift (through pop) we need to revert
            # back to the state before the shift (which can be found on the top
            # of the stack after the "pop"
            if isinstance(node.symbol, FinishSymbol):
                # if we reached the end of the stack, reset to state 0 and push
                # FinishSymbol pack onto the stack
                self.current_state = 0
                self.stack.append(node)
                return
            else:
                self.current_state = self.stack[-1].state
        self.shift(node)

    def shift(self, la):
        # after the breakdown, we need to properly shift the left over terminal
        # using the (correct) current state from before the optimistic shift of
        # it's parent tree
        lookup_symbol = self.get_lookup(la)
        element = self.syntaxtable.lookup(self.current_state, lookup_symbol)
        logging.debug("RBShift: la: %s state: %s element: %s", la, la.state, element)
        la.state = element.action
        self.stack.append(la)
        logging.debug("RBShift: set state to %s", la.state)
        self.current_state = la.state

    def pop_lookahead(self, la):
        while(la.right_sibling() is None):
            la = la.parent
        return la.right_sibling()

    def shiftable(self, la):
        if self.syntaxtable.lookup(self.current_state, la.symbol):
            return True
        return False

    def has_changed(self, node):
        return node in self.all_changes

    def prepare_input(self, _input):
        l = []
        # XXX need an additional lexer to do this right
        if _input != "":
            for i in _input.split(" "):
                l.append(Terminal(i))
        l.append(FinishSymbol())
        return l

    def get_ast(self):
        bos = Node(Terminal("bos"), 0, [])
        eos = Node(FinishSymbol(), 0, [])
        root = Node(Nonterminal("Root"), 0, [bos, self.ast_stack[0], eos])
        return AST(root)

    def get_next_possible_symbols(self, state_id):
        l = set()
        for (state, symbol) in self.syntaxtable.table.keys():
            if state == state_id:
                l.add(symbol)
        return l

    def get_next_symbols_list(self, state = -1):
        if state == -1:
            state = self.last_shift_state
        lookahead = self.get_next_possible_symbols(state)

        s = []
        for symbol in lookahead:
            s.append(symbol.name)
        return s

    def get_next_symbols_string(self, state = -1):
        l = self.get_next_symbols_list(state)
        return ", ".join(l)

    def get_expected_symbols(self, state_id):
        #XXX if state of a symbol is nullable, return next symbol as well
        #XXX if at end of state, find state we came from (reduce, stack) and get next symbols from there
        if state_id != -1:
            stateset = self.graph.state_sets[state_id]
            symbols = stateset.get_next_symbols_no_ws()
            return symbols
        return []


    def reset(self):
        self.stack = []
        self.ast_stack = []
        self.all_changes = []
        self.undo = []
        self.last_shift_state = 0
        self.validating = False
        self.last_status = False
        self.error_node = None
        self.previous_version = None
        self.init_ast()

    def load_status(self, version):
        try:
            self.last_status = self.status_by_version[version]
        except KeyError:
            logging.warning("Could not find status for version %s", version)
        try:
            self.error_node = self.errornode_by_version[version]
        except KeyError:
            logging.warning("Could not find errornode for version %s", version)

    def save_status(self, version):
        self.status_by_version[version] = self.last_status
        self.errornode_by_version[version] = self.error_node
