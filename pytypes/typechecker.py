'''
Created on 20.08.2016

@author: Stefan Richthofer
'''

import sys, typing, inspect, re, atexit
from inspect import isclass, ismodule, isfunction, ismethod, ismethoddescriptor
from .stubfile_manager import _match_stub_type
from .type_util import type_str, has_type_hints, is_builtin_type, \
		_methargtype, deep_type, _funcsigtypes
from . import util, InputTypeError, ReturnTypeError, OverrideError
import pytypes

if sys.version_info.major >= 3:
	import builtins
else:
	import __builtin__ as builtins

not_type_checked = set()

_delayed_checks = []

# Monkeypatch import to process forward-declarations after module loading finished:
savimp = builtins.__import__
def newimp(name, *x):
	res = savimp(name, *x)
	_run_delayed_checks(True, name)
	return res
builtins.__import__ = newimp

class _DelayedCheck():
	def __init__(self, func, method, class_name, base_method, base_class, exc_info):
		self.func = func
		self.method = method
		self.class_name = class_name
		self.base_method = base_method
		self.base_class = base_class
		self.exc_info = exc_info
		self.raising_module_name = func.__module__

	def run_check(self, raise_NameError = False):
		if raise_NameError:
			meth_types = _funcsigtypes(self.func, True, self.base_class)
			_check_override_types(self.method, meth_types, self.class_name,
					self.base_method, self.base_class)
		else:
			try:
				meth_types = _funcsigtypes(self.func, True, self.base_class)
				_check_override_types(self.method, meth_types, self.class_name,
						self.base_method, self.base_class)
			except NameError:
				pass


def _run_delayed_checks(raise_NameError = False, module_name = None):
	global _delayed_checks
	if module_name is None:
		to_run = _delayed_checks
		_delayed_checks = []
	else:
		new_delayed_checks = []
		to_run = []
		for check in _delayed_checks:
			if check.raising_module_name.startswith(module_name):
				to_run.append(check)
			else:
				new_delayed_checks.append(check)
		_delayed_checks = new_delayed_checks
	for check in to_run:
		check.run_check(raise_NameError)

atexit.register(_run_delayed_checks, True)

def _check_override_types(method, meth_types, class_name, base_method, base_class):
	base_types = _match_stub_type(_funcsigtypes(base_method, True, base_class))
	meth_types = _match_stub_type(meth_types)
	if has_type_hints(base_method):
		if not issubclass(base_types[0], meth_types[0]):
			fq_name_child = util._fully_qualified_func_name(method, True, None, class_name)
			fq_name_parent = util._fully_qualified_func_name(base_method, True, base_class)
			#assert fq_name_child == ('%s.%s.%s' % (method.__module__, class_name, method.__name__))
			#assert fq_name_parent == ('%s.%s.%s' % (base_method.__module__, base_class.__name__, base_method.__name__))

			raise OverrideError('%s cannot override %s.\n'
					% (fq_name_child, fq_name_parent)
					+ 'Incompatible argument types: %s is not a supertype of %s.'
					% (type_str(meth_types[0]), type_str(base_types[0])))
		if not issubclass(meth_types[1], base_types[1]):
			fq_name_child = util._fully_qualified_func_name(method, True, None, class_name)
			fq_name_parent = util._fully_qualified_func_name(base_method, True, base_class)
			#assert fq_name_child == ('%s.%s.%s' % (method.__module__, class_name, method.__name__))
			#assert fq_name_parent == ('%s.%s.%s' % (base_method.__module__, base_class.__name__, base_method.__name__))

			raise OverrideError('%s cannot override %s.\n'
					% (fq_name_child, fq_name_parent)
					+ 'Incompatible result types: %s is not a subtype of %s.'
					% (type_str(meth_types[1]), type_str(base_types[1])))

def _check_override_argspecs(method, argSpecs, class_name, base_method, base_class):
	ovargs = util.getargspecs(base_method)
	d1 = 0 if ovargs.defaults is None else len(ovargs.defaults)
	d2 = 0 if argSpecs.defaults is None else len(argSpecs.defaults)
	if len(ovargs.args)-d1 < len(argSpecs.args)-d2 or len(ovargs.args) > len(argSpecs.args):
		fq_name_child = util._fully_qualified_func_name(method, True, None, class_name)
		fq_name_parent = util._fully_qualified_func_name(base_method, True, base_class)
		#assert fq_name_child == ('%s.%s.%s' % (method.__module__, class_name, method.__name__))
		#assert fq_name_parent == ('%s.%s.%s' % (base_method.__module__, base_class.__name__, base_method.__name__))

		raise OverrideError('%s cannot override %s:\n'
				% (fq_name_child, fq_name_parent)
				+ 'Mismatching argument count. Base-method: %i+%i   submethod: %i+%i'
				% (len(ovargs.args)-d1, d1, len(argSpecs.args)-d2, d2))

def _no_base_method_error(method):
	return OverrideError('%s in %s does not override any other method.\n'
					% (method.__name__, method.__module__))

def _function_instead_of_method_error(method):
	return OverrideError('@override was applied to a function, not a method: %s.%s.\n'
					% (method.__module__, method.__name__))

def override(func):
	if not pytypes.checking_enabled:
		return func
	# notes:
	# - don't use @override on __init__ (raise warning? Error for now!),
	#   because __init__ is not intended to be called after creation
	# - @override applies typechecking to every match in mro, because class might be used as
	#   replacement for each class in its mro. So each must be compatible.
	# - @override does not/cannot check signature of builtin ancestors (for now).
	# - @override starts checking only at its declaration level. If in a subclass an @override
	#   annotated method is not s.t. @override any more.
	#   This is difficult to achieve in case of a call to super. Runtime-override checking
	#   would use the subclass-self and thus unintentionally would also check the submethod's
	#   signature. We actively avoid this here.
	if pytypes.check_override_at_class_definition_time:
		# We need some trickery here, because details of the class are not yet available
		# as it is just getting defined. Luckily we can get base-classes via inspect.stack():
		stack = inspect.stack()
		try:
			base_classes = re.search(r'class.+\((.+)\)\s*\:', stack[2][4][0]).group(1)
		except IndexError:
			raise _function_instead_of_method_error(func)
		meth_cls_name = stack[1][3]
		if func.__name__ == '__init__':
			raise OverrideError(
					'Invalid use of @override in %s:\n  @override must not be applied to __init__.'
					% util._fully_qualified_func_name(func, True, None, meth_cls_name))
		# handle multiple inheritance
		base_classes = [s.strip() for s in base_classes.split(',')]
		if not base_classes:
			raise ValueError('@override: unable to determine base class') 

		# stack[0]=overrides, stack[1]=inside class def'n, stack[2]=outside class def'n
		derived_class_locals = stack[2][0].f_locals
		derived_class_globals = stack[2][0].f_globals

		# replace each class name in base_classes with the actual class type
		for i, base_class in enumerate(base_classes):
			if '.' not in base_class:
				if base_class in derived_class_locals:
					base_classes[i] = derived_class_locals[base_class]
				else:
					base_classes[i] = derived_class_globals[base_class]
			else:
				components = base_class.split('.')
				# obj is either a module or a class
				if components[0] in derived_class_locals:
					obj = derived_class_locals[components[0]]
				else:
					obj = derived_class_globals[components[0]]
				for c in components[1:]:
					assert(ismodule(obj) or isclass(obj))
					obj = getattr(obj, c)
				base_classes[i] = obj

		mro_set = set() # contains everything in would-be-mro, however in unspecified order
		mro_pool = [base_classes]
		while len(mro_pool) > 0:
			lst = mro_pool.pop()
			for base_cls in lst:
				if not is_builtin_type(base_cls):
					mro_set.add(base_cls)
					mro_pool.append(base_cls.__bases__)

		base_method_exists = False
		argSpecs = util.getargspecs(func)
		for cls in mro_set:
			if hasattr(cls, func.__name__):
				base_method_exists = True
				base_method = getattr(cls, func.__name__)
				_check_override_argspecs(func, argSpecs, meth_cls_name, base_method, cls)
				if has_type_hints(func):
					try:
						_check_override_types(func, _funcsigtypes(func, True, cls), meth_cls_name,
								base_method, cls)
					except NameError:
						_delayed_checks.append(_DelayedCheck(func, func, meth_cls_name, base_method,
								cls, sys.exc_info()))
		if not base_method_exists:
			raise _no_base_method_error(func)

	if pytypes.check_override_at_runtime:
		def checker_ov(*args, **kw):
			argSpecs = util.getargspecs(func)

			args_kw = args
			if len(kw) > 0:
				args_kw = tuple([t for t in args] + [kw[name] for name in argSpecs.args[len(args):]])

			if len(argSpecs.args) > 0 and argSpecs.args[0] == 'self':
				if hasattr(args_kw[0].__class__, func.__name__) and \
						ismethod(getattr(args_kw[0], func.__name__)):
					actual_class = args_kw[0].__class__
					if util._actualfunc(getattr(args_kw[0], func.__name__)) != func:
						for acls in args_kw[0].__class__.__mro__:
							if not is_builtin_type(acls):
								if hasattr(acls, func.__name__) and func.__name__ in acls.__dict__ and \
										util._actualfunc(acls.__dict__[func.__name__]) == func:
									actual_class = acls
					if func.__name__ == '__init__':
						raise OverrideError(
								'Invalid use of @override in %s:\n    @override must not be applied to __init__.'
								% util._fully_qualified_func_name(func, True, actual_class))
					ovmro = []
					base_method_exists = False
					for mc in actual_class.__mro__[1:]:
						if hasattr(mc, func.__name__):
							ovf = getattr(mc, func.__name__)
							base_method_exists = True
							if not is_builtin_type(mc):
								ovmro.append(mc)
					if not base_method_exists:
						raise _no_base_method_error(func)
					# Not yet support overloading
					# Check arg-count compatibility
					for ovcls in ovmro:
						ovf = getattr(ovcls, func.__name__)
						_check_override_argspecs(func, argSpecs, actual_class.__name__, ovf, ovcls)
					# Check arg/res-type compatibility
					meth_types = _funcsigtypes(func, True, args_kw[0].__class__)
					if has_type_hints(func):
						for ovcls in ovmro:
							ovf = getattr(ovcls, func.__name__)
							_check_override_types(func, meth_types, actual_class.__name__, ovf, ovcls)
				else:
					raise OverrideError('@override was applied to a non-method: %s.%s.\n'
						% (func.__module__, func.__name__)
						+ "that declares 'self' although not a method.")
			else:
				raise _function_instead_of_method_error(func)
			return func(*args, **kw)
	
		checker_ov.ov_func = func
		if hasattr(func, '__func__'):
			checker_ov.__func__ = func.__func__
		checker_ov.__name__ = func.__name__ # What sorts of evil might this bring over us?
		checker_ov.__module__ = func.__module__
		checker_ov.__globals__.update(func.__globals__)
		if hasattr(func, '__annotations__'):
			checker_ov.__annotations__ = func.__annotations__
		if hasattr(func, '__qualname__'):
			checker_ov.__qualname__ = func.__qualname__
		checker_ov.__doc__ = func.__doc__
		# Todo: Check what other attributes might be needed (e.g. by debuggers).
		checker_ov._check_parent_types = True
		return checker_ov
	else:
		func._check_parent_types = True
		return func

def _make_type_error_message(tp, func, slf, func_class, expected_tp, incomp_text):
	_cmp_msg_format = 'Expected: %s\nReceived: %s'
	fq_func_name = util._fully_qualified_func_name(func, slf, func_class)
	if slf:
		#Todo: Clarify if an @override-induced check caused this
		# Todo: Python3 misconcepts method as classmethod here, because it doesn't
		# detect it as bound method, because ov_checker or tp_checker obfuscate it
		if not func_class is None and not type(func) is classmethod:
			func = getattr(func_class, func.__name__)
		if hasattr(func, 'im_class'):
			return fq_func_name+' '+incomp_text+':\n'+_cmp_msg_format \
				% (type_str(expected_tp), type_str(tp))
		else:
			return fq_func_name+' '+incomp_text+':\n'+_cmp_msg_format \
				% (type_str(expected_tp), type_str(tp))
	elif type(func) == staticmethod:
		return fq_func_name+' '+incomp_text+':\n'+_cmp_msg_format \
				% (type_str(expected_tp), type_str(tp))
	else:
		return fq_func_name+' '+incomp_text+':\n'+_cmp_msg_format \
				% (type_str(expected_tp), type_str(tp))

def _checkfunctype(tp, func, slf, func_class):
	argSig, resSig = _funcsigtypes(func, slf, func_class)
	if not issubclass(tp, _match_stub_type(argSig)):
		raise InputTypeError(_make_type_error_message(tp, func, slf, func_class,
				argSig, 'called with incompatible types'))
	return _match_stub_type(resSig) # provide this by-product for potential future use

def _checkfuncresult(resSig, tp, func, slf, func_class):
	if not issubclass(tp, _match_stub_type(resSig)):
		raise ReturnTypeError(_make_type_error_message(tp, func, slf, func_class,
				resSig, 'returned incompatible type'))

def typechecked_func(func, force = False):
	if not pytypes.checking_enabled:
		return func
	assert(isfunction(func) or ismethod(func) or ismethoddescriptor(func))
	if not force and is_no_type_check(func):
		return func
	clsm = type(func) == classmethod
	stat = type(func) == staticmethod
	func0 = util._actualfunc(func)

	if hasattr(func, '_check_parent_types'):
		checkParents = func._check_parent_types
	else:
		checkParents = False

	def checker_tp(*args, **kw):
		# check consistency regarding special case with 'self'-keyword
		slf = False

		args_kw = args
		argNames = util.getargspecs(func0).args
		if len(kw) > 0:
			args_kw = tuple([t for t in args] + [kw[name] for name in argNames[len(args):]])

		if len(argNames) > 0:
			if clsm:
				if argNames[0] != 'cls':
					print('Warning: classmethod using non-idiomatic argname '+func0.__name__)
				tp = _methargtype(args_kw)
			elif argNames[0] == 'self':
				if hasattr(args_kw[0].__class__, func0.__name__) and \
						ismethod(getattr(args_kw[0], func0.__name__)):
					tp = _methargtype(args_kw)
					slf = True
				else:
					print('Warning: non-method declaring self '+func0.__name__)
					tp = deep_type(args_kw)
			else:
				tp = deep_type(args_kw)
		else:
			tp = deep_type(args_kw)
			
		if checkParents:
			if not slf:
				raise OverrideError('@override with non-instancemethod not supported: %s.%s.%s.\n'
					% (func0.__module__, args_kw[0].__class__.__name__, func0.__name__))
			toCheck = []
			for cls in args_kw[0].__class__.__mro__:
				if hasattr(cls, func0.__name__):
					ffunc = getattr(cls, func0.__name__)
					if has_type_hints(util._actualfunc(ffunc)):
						toCheck.append(ffunc)
		else:
			toCheck = (func,)

		parent_class = None
		if slf:
			parent_class = args_kw[0].__class__
		elif clsm:
			parent_class = args_kw[0]

		resSigs = []
		for ffunc in toCheck:
			resSigs.append(_checkfunctype(tp, ffunc, slf or clsm, parent_class))

		# perform backend-call:
		if clsm or stat:
			res = func.__func__(*args, **kw)
		else:
			res = func(*args, **kw)
		
		tp = deep_type(res)
		for i in range(len(resSigs)):
			_checkfuncresult(resSigs[i], tp, toCheck[i], slf or clsm, parent_class)
		return res

	checker_tp.ch_func = func
	if hasattr(func, '__func__'):
		checker_tp.__func__ = func.__func__
	checker_tp.__name__ = func0.__name__ # What sorts of evil might this bring over us?
	checker_tp.__module__ = func0.__module__
	checker_tp.__globals__.update(func0.__globals__)
	if hasattr(func, '__annotations__'):
		checker_tp.__annotations__ = func.__annotations__
	if hasattr(func, '__qualname__'):
		checker_tp.__qualname__ = func.__qualname__
	checker_tp.__doc__ = func.__doc__
	# Todo: Check what other attributes might be needed (e.g. by debuggers).
	if clsm:
		return classmethod(checker_tp)
	elif stat:
		return staticmethod(checker_tp)
	else:
		return checker_tp

def typechecked_class(cls, force = False, force_recursive = False):
	if not pytypes.checking_enabled:
		return cls
	assert(isclass(cls))
	if not force and is_no_type_check(cls):
		return cls
	# To play it safe we avoid to modify the dict while iterating over it,
	# so we previously cache keys.
	# For this we don't use keys() because of Python 3.
	keys = [key for key in cls.__dict__]
	for key in keys:
		obj = cls.__dict__[key]
		if force_recursive or not is_no_type_check(obj):
			if isfunction(obj) or ismethod(obj) or ismethoddescriptor(obj):
				setattr(cls, key, typechecked_func(obj, force_recursive))
			elif isclass(obj):
				setattr(cls, key, typechecked_class(obj, force_recursive, force_recursive))
	return cls

# Todo: Write tests for this
def typechecked_module(md, force_recursive = False):
	'''
	Intended to typecheck modules that were not annotated with @typechecked without
	modifying their code.
	'''
	if not pytypes.checking_enabled:
		return md
	assert(ismodule(md))
	# To play it safe we avoid to modify the dict while iterating over it,
	# so we previously cache keys.
	# For this we don't use keys() because of Python 3.
	keys = [key for key in md.__dict__]
	for key in keys:
		obj = md.__dict__[key]
		if force_recursive or not is_no_type_check(obj):
			if isfunction(obj) or ismethod(obj) or ismethoddescriptor(obj):
				setattr(md, key, typechecked_func(obj, force_recursive))
			elif isclass(obj):
				setattr(md, key, typechecked_class(obj, force_recursive, force_recursive))

def typechecked(obj):
	if not pytypes.checking_enabled:
		return obj
	if is_no_type_check(obj):
		return obj
	if isfunction(obj) or ismethod(obj) or ismethoddescriptor(obj):
		return typechecked_func(obj)
	if isclass(obj):
		return typechecked_class(obj)
	return obj

def no_type_check(obj):
	try:
		return typing.no_type_check(obj)
	except(AttributeError):
		not_type_checked.add(obj)
		return obj

def is_no_type_check(obj):
	return (hasattr(obj, '__no_type_check__') and obj.__no_type_check__) or obj in not_type_checked
