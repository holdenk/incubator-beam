#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Trivial type inference for simple functions.

For internal use only; no backwards-compatibility guarantees.
"""
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

from builtins import zip
from builtins import str
from past.utils import old_div
from builtins import object
import builtins
import collections
import dis
import pprint
import sys
import types
from functools import reduce

from apache_beam.typehints import Any
from apache_beam.typehints import typehints


class TypeInferenceError(ValueError):
  """Error to raise when type inference failed."""
  pass


def instance_to_type(o):
  """Given a Python object o, return the corresponding type hint.
  """
  t = type(o)
  if o is None:
    return type(None)
  elif t not in typehints.DISALLOWED_PRIMITIVE_TYPES:
    if t == types.InstanceType:
      return o.__class__
    elif t == BoundMethod:
      return types.MethodType
    return t
  elif t == tuple:
    return typehints.Tuple[[instance_to_type(item) for item in o]]
  elif t == list:
    return typehints.List[
        typehints.Union[[instance_to_type(item) for item in o]]
    ]
  elif t == set:
    return typehints.Set[
        typehints.Union[[instance_to_type(item) for item in o]]
    ]
  elif t == dict:
    return typehints.Dict[
        typehints.Union[[instance_to_type(k) for k, v in o.items()]],
        typehints.Union[[instance_to_type(v) for k, v in o.items()]],
    ]
  else:
    raise TypeInferenceError('Unknown forbidden type: %s' % t)


def union_list(xs, ys):
  assert len(xs) == len(ys)
  return [union(x, y) for x, y in zip(xs, ys)]


class Const(object):

  def __init__(self, value):
    self.value = value
    self.type = instance_to_type(value)

  def __eq__(self, other):
    return isinstance(other, Const) and self.value == other.value

  def __hash__(self):
    return hash(self.value)

  def __repr__(self):
    return 'Const[%s]' % str(self.value)[:100]

  @staticmethod
  def unwrap(x):
    if isinstance(x, Const):
      return x.type
    return x

  @staticmethod
  def unwrap_all(xs):
    return [Const.unwrap(x) for x in xs]


class FrameState(object):
  """Stores the state of the frame at a particular point of execution.
  """

  def __init__(self, f, local_vars=None, stack=()):
    self.f = f
    if sys.version_info[0] >= 3:
      self.co = f.__code__
    else:
      self.co = f.func_code
    self.vars = list(local_vars)
    self.stack = list(stack)

  def __eq__(self, other):
    return self.__dict__ == other.__dict__

  def copy(self):
    return FrameState(self.f, self.vars, self.stack)

  def const_type(self, i):
    return Const(self.co.co_consts[i])

  def closure_type(self, i):
    ncellvars = len(self.co.co_cellvars)
    if i < ncellvars:
      return Any
    return Const(self.f.__closure__[i - ncellvars].cell_contents)

  def get_global(self, i):
    name = self.get_name(i)
    if name in self.f.__globals__:
      return Const(self.f.__globals__[name])
    if name in builtins.__dict__:
      return Const(builtins.__dict__[name])
    return Any

  def get_name(self, i):
    return self.co.co_names[i]

  def __repr__(self):
    return 'Stack: %s Vars: %s' % (self.stack, self.vars)

  def __or__(self, other):
    if self is None:
      return other.copy()
    elif other is None:
      return self.copy()
    return FrameState(self.f, union_list(self.vars, other.vars), union_list(
        self.stack, other.stack))

  def __ror__(self, left):
    return self | left


def union(a, b):
  """Returns the union of two types or Const values.
  """
  if a == b:
    return a
  elif not a:
    return b
  elif not b:
    return a
  a = Const.unwrap(a)
  b = Const.unwrap(b)
  # TODO(robertwb): Work this into the Union code in a more generic way.
  if type(a) == type(b) and element_type(a) == typehints.Union[()]:
    return b
  elif type(a) == type(b) and element_type(b) == typehints.Union[()]:
    return a
  return typehints.Union[a, b]


def element_type(hint):
  """Returns the element type of a composite type.
  """
  print("input hint %s " % hint)
  hint = Const.unwrap(hint)
  print("unwrapped %s " % hint)
  if isinstance(hint, typehints.SequenceTypeConstraint):
    print("sequence type")
    return hint.inner_type
  elif isinstance(hint, typehints.TupleHint.TupleConstraint):
    print("tuple constraint")
    return typehints.Union[hint.tuple_types]
  return Any


def key_value_types(kv_type):
  """Returns the key and value type of a KV type.
  """
  # TODO(robertwb): Unions of tuples, etc.
  # TODO(robertwb): Assert?
  if (isinstance(kv_type, typehints.TupleHint.TupleConstraint)
      and len(kv_type.tuple_types) == 2):
    return kv_type.tuple_types
  return Any, Any


known_return_types = {len: int, hash: int,}


class BoundMethod(object):
  """Used to create a bound method when we only know the type of the instance.
  """

  def __init__(self, unbound):
    self.unbound = unbound


def hashable(c):
  try:
    hash(c)
    return True
  except TypeError:
    return False


def infer_return_type(c, input_types, debug=True, depth=5):
  """Analyses a callable to deduce its return type.

  Args:
    c: A Python callable to infer the return type of.
    input_types: A sequence of inputs corresponding to the input types.
    debug: Whether to print verbose debugging information.
    depth: Maximum inspection depth during type inference.

  Returns:
    A TypeConstraint that that the return value of this function will (likely)
    satisfy given the specified inputs.
  """
  print("\nhi!\n")
  print("infering type %s" % c)
  try:
    if hashable(c) and c in known_return_types:
      return known_return_types[c]
    elif isinstance(c, types.FunctionType):
      print("I'm a function!\n")
      return infer_return_type_func(c, input_types, debug, depth)
    elif isinstance(c, types.MethodType):
      if c.__self__ is not None:
        input_types = [Const(c.__self__)] + input_types
      return infer_return_type_func(c.__func__, input_types, debug, depth)
    elif isinstance(c, BoundMethod):
      unbound = c.unbound
      print("unbound is %s %s %s" % (unbound, type(unbound), dir(unbound)))
      # Python 2/3 compatibility.
      method_class = getattr(c.unbound, "__self__", c.unbound).__class__
      func = getattr(c.unbound, "__func__", c.unbound)
      input_types = [method_class] + input_types
      return infer_return_type_func(
          func, input_types, debug, depth)
    elif isinstance(c, type):
      if c in typehints.DISALLOWED_PRIMITIVE_TYPES:
        print("Disallowed primitive type %s" % c)
        return {
            list: typehints.List[Any],
            set: typehints.Set[Any],
            tuple: typehints.Tuple[Any, ...],
            dict: typehints.Dict[Any, Any]
        }[c]
      print("Allowed primitive time %s" % c)
      return c
    else:
      return Any
  except TypeInferenceError:
    return Any
  except Exception:
    if debug:
      sys.stdout.flush()
      raise
    else:
      return Any


def infer_return_type_func(f, input_types, debug=True, depth=0):
  """Analyses a function to deduce its return type.

  Args:
    f: A Python function object to infer the return type of.
    input_types: A sequence of inputs corresponding to the input types.
    debug: Whether to print verbose debugging information.
    depth: Maximum inspection depth during type inference.

  Returns:
    A TypeConstraint that that the return value of this function will (likely)
    satisfy given the specified inputs.

  Raises:
    TypeInferenceError: if no type can be inferred.
  """
  debug = True
  if debug:
    print()
    print(f, id(f), input_types)
  from . import opcodes
  simple_ops = dict((k.upper(), v) for k, v in opcodes.__dict__.items())

  co = f.__code__
  code = co.co_code
  end = len(code)
  pc = 0
  extended_arg = 0
  free = None

  yields = set()
  returns = set()
  # TODO(robertwb): Default args via inspect module.
  try:
    input_types_list = list(input_types)
  except TypeError:
    input_types_list = [input_types]
  typehints_union = [typehints.Union[()]]
  target_length = len(co.co_varnames) - len(input_types_list)
  local_vars = input_types_list + typehints_union * target_length
  state = FrameState(f, local_vars)
  states = collections.defaultdict(lambda: None)
  jumps = collections.defaultdict(int)

  last_pc = -1
  while pc < end:
    start = pc
    if sys.version_info[0] >= 3:
      op = code[pc]
    else:
      op = ord(code[pc])

    if debug:
      print('-->' if pc == last_pc else '    ', end=' ')
      print(repr(pc).rjust(4), end=' ')
      print(dis.opname[op].ljust(20), end=' ')
    pc += 1
    if op >= dis.HAVE_ARGUMENT:
      if sys.version_info[0] >= 3:
        arg = code[pc] + code[pc + 1] * 256 + extended_arg
      else:
        arg = ord(code[pc]) + ord(code[pc + 1]) * 256 + extended_arg
      extended_arg = 0
      pc += 2
      if op == dis.EXTENDED_ARG:
        extended_arg = arg * 65536
      if debug:
        print(str(arg).rjust(5), end=' ')
        if op in dis.hasconst:
          print('(' + repr(co.co_consts[arg]) + ')', end=' ')
        elif op in dis.hasname:
          print('(' + co.co_names[arg] + ')', end=' ')
        elif op in dis.hasjrel:
          print('(to ' + repr(pc + arg) + ')', end=' ')
        elif op in dis.haslocal:
          print('(' + co.co_varnames[arg] + ')', end=' ')
        elif op in dis.hascompare:
          print('(' + dis.cmp_op[arg] + ')', end=' ')
        elif op in dis.hasfree:
          if free is None:
            free = co.co_cellvars + co.co_freevars
          print('(' + free[arg] + ')', end=' ')

    # Acutally emulate the op.
    if state is None and states[start] is None:
      # No control reaches here (yet).
      if debug:
        print()
      continue
    state |= states[start]

    opname = dis.opname[op]
    jmp = jmp_state = None
    print("Opname %s" % opname)
    if opname.startswith('CALL_FUNCTION'):
      print("Calling function!")
      standard_args = (arg & 0xF) + old_div((arg & 0xF0), 8)
      var_args = 'VAR' in opname
      kw_args = 'KW' in opname
      pop_count = standard_args + var_args + kw_args + 1
      print("%s, %s, %s, %s" % (standard_args, var_args, kw_args, pop_count))
      if depth <= 0:
        print("Depth 0")
        return_type = Any
      elif arg & 0xF0:
        print("idk wtf this is")
        # TODO(robertwb): Handle this case.
        return_type = Any
      elif (isinstance(state.stack[-pop_count], Const) and
            state.stack[-pop_count].value == list):
        print("oook?")
        # TODO(robertwb + holden): Handle this better.
        return_type = typehints.List[element_type(state.stack[1])]
      elif isinstance(state.stack[-pop_count], Const):
        print("boople")
        # TODO(robertwb): Handle this better.
        if var_args or kw_args:
          state.stack[-1] = Any
          state.stack[-var_args - kw_args] = Any
        return_type = infer_return_type(state.stack[-pop_count].value,
                                        state.stack[1 - pop_count:],
                                        debug=debug,
                                        depth=depth - 1)
      else:
        print("boop boop")
        print("aww yeah? arg is %s and stack minus pop count is %s" %
              (arg, state.stack[-pop_count]))
        print("v%s" % type(state.stack[-pop_count]))
        return_type = Any
      print("mok?")
      state.stack[-pop_count:] = [return_type]
    elif (opname == 'BINARY_SUBSCR'
          and isinstance(state.stack[1], Const)
          and isinstance(state.stack[0], typehints.IndexableTypeConstraint)):
      if debug:
        print("Executing special case binary subscript")
      idx = state.stack.pop()
      src = state.stack.pop()
      try:
        state.stack.append(src._constraint_for_index(idx.value))
      except Exception as e:
        if debug:
          print("Exception {0} during special case indexing".format(e))
        state.stack.append(Any)
    elif opname in simple_ops:
      if debug:
        print("Executing simple %s op with state %s and arg %s" %
              (opname, state, arg))
      simple_ops[opname](state, arg)
    elif opname == 'RETURN_VALUE':
      returns.add(state.stack[-1])
      state = None
    elif opname == 'YIELD_VALUE':
      yields.add(state.stack[-1])
    elif opname == 'JUMP_FORWARD':
      jmp = pc + arg
      jmp_state = state
      state = None
    elif opname == 'JUMP_ABSOLUTE':
      jmp = arg
      jmp_state = state
      state = None
    elif opname in ('POP_JUMP_IF_TRUE', 'POP_JUMP_IF_FALSE'):
      state.stack.pop()
      jmp = arg
      jmp_state = state.copy()
    elif opname in ('JUMP_IF_TRUE_OR_POP', 'JUMP_IF_FALSE_OR_POP'):
      print("jumping...")
      jmp = arg
      jmp_state = state.copy()
      state.stack.pop()
    elif opname == 'FOR_ITER':
      print("for iter")
      jmp = pc + arg
      jmp_state = state.copy()
      jmp_state.stack.pop()
      state.stack.append(element_type(state.stack[-1]))
    elif opname == 'BUILD_LIST':
      print("building list")
      jmp = pc + arg
      jmp_state = state.copy()
      jmp_state.stack.pop()
      state.stack.append(element_type(state.stack[-1]))
    else:
      print("fail")
      raise TypeInferenceError('unable to handle %s' % opname)

    if jmp is not None:
      print("buttons")
      # TODO(robertwb): Is this guerenteed to converge?
      new_state = states[jmp] | jmp_state
      if jmp < pc and new_state != states[jmp] and jumps[pc] < 5:
        jumps[pc] += 1
        pc = jmp
      states[jmp] = new_state

    if debug:
      print()
      print(state)
      pprint.pprint(dict(item for item in states.items() if item[1]))

  if yields:
    result = typehints.Iterable[reduce(union, Const.unwrap_all(yields))]
  else:
    result = reduce(union, Const.unwrap_all(returns))

  if debug:
    print(f, id(f), input_types, '->', result)
  return result
