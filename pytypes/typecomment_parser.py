'''
Created on 13.12.2016

@author: Stefan Richthofer
'''

import inspect
import pytypes
from typing import Any
from pytypes import TypeSyntaxError

def _striptrailingcomment(s):
	pos = s.find('#')
	if pos > -1:
		return s[:pos].strip()
	else:
		return s.strip()

def _parse_typecomment_oneline(line):
	commStart = line.find('#')
	tp_delim = 'type'
	if commStart > -1 and len(line) > commStart+1:
		comment = line[commStart+1:].strip()
		if (comment.startswith(tp_delim) and len(comment) > len(tp_delim)+1):
			comment = comment[len(tp_delim):].strip()
			if (comment.startswith(':')):
				comment = comment[1:].strip()
				if len(comment) > 0:
					return comment
	return None

def _get_typestrings(obj, slf):
	try:
		srclines = inspect.getsourcelines(obj)[0]
	except IOError:
		return None
	funcstart = 0
	startInit = False
	result = []
	for line in srclines:
		ln = _striptrailingcomment(line)
		if len(ln) > 0:
			if ln.startswith('def '):
				startInit = True
			if startInit:
				if ln.endswith(':'):
					if ln[:-1].strip().endswith(')') or ln.find('->') != -1:
						break
				elif not ln[-1] == '(':
					result.append(_parse_typecomment_oneline(line))
		funcstart += 1
	if len(srclines) <= funcstart:
		return None
	res = _parse_typecomment_oneline(srclines[funcstart])
	if not res is None:
		return res, result[1:] if slf else result
	if len(srclines) > funcstart+1:
		strp = srclines[funcstart+1].strip()
		if len(strp) > 0 and strp[0] == '#':
			res = _parse_typecomment_oneline(srclines[funcstart+1]), result[1:] if slf else result
			return res
	return None, result[1:] if slf else result

def _isargsellipsis(argStr):
	return argStr[1:-1].strip() == '...'

def _make_typestring_err_msg(msg, typestring, func, slf, func_class):
	fq_func_name = pytypes.util._fully_qualified_func_name(func, slf, func_class)
	return '\n  '+fq_func_name+'\n  '+msg+':\n'+typestring

def _check_vararg_typestring(typestring, argString, argspec, func, slf, func_class):
	vkw_count = argString.count('**')
	args = None
	if vkw_count > 1:
		raise TypeSyntaxError(_make_typestring_err_msg(
				"Typestring contains multiple var-keywords ('**') args",
				typestring, func, slf, func_class))
	if vkw_count > 0:
		args = argString.strip()[1:-1].split(',')
		argString = argString.replace('**', '')
	varg_count = argString.count('*')
	if varg_count > 1:
		raise TypeSyntaxError(_make_typestring_err_msg(
				"Typestring contains multiple var-length ('*') args",
				typestring, func, slf, func_class))
	if varg_count > 0:
		if args is None:
			args = argString.strip()[1:-1].split(',')
		argString = argString.replace('*', '')
	if not args is None:
		tpnames = pytypes.util.getargnames(argspec)
		if slf:
			#if not tpnames[0] == 'self':
				#todo: maybe warn here
			tpnames = tpnames[1:]
		idx = 0
		assert_count = 0
		assert_count_kw = 0
		for arg in args:
			if arg.strip().startswith('**'):
				assert_count_kw += 1
				kw_msg = "'**' misplaced in typestring"
				try:
					if not tpnames[idx] == argspec.varkw:
						raise TypeSyntaxError(_make_typestring_err_msg(
								kw_msg, typestring, func, slf, func_class))
				except AttributeError:
					if not tpnames[idx] == argspec.keywords:
						raise TypeSyntaxError(_make_typestring_err_msg(
								kw_msg, typestring, func, slf, func_class))
			elif arg.strip().startswith('*'):
				assert_count += 1
				if idx >= len(tpnames) or not tpnames[idx] == argspec.varargs:
					raise TypeSyntaxError(_make_typestring_err_msg(
							"'*' misplaced in typestring", typestring, func, slf, func_class))
			idx += 1
		if varg_count != assert_count or vkw_count != assert_count_kw:
			raise TypeSyntaxError(_make_typestring_err_msg(
					'Invalid typestring syntax', typestring, func, slf, func_class))

	if vkw_count == 1:
		vkw = None
		try:
			vkw = argspec.varkw
		except AttributeError:
			vkw = argspec.keywords
		if vkw is None:
			raise TypeSyntaxError(_make_typestring_err_msg(
					'Typestring lists var-keywords not declared in %s header.'
					% ('method' if slf else 'function'),
					typestring, func, slf, func_class))
	elif vkw_count == 0:
		try:
			vkw = argspec.varkw
		except AttributeError:
			vkw = argspec.keywords
		if not vkw is None:
			raise TypeSyntaxError(_make_typestring_err_msg(
					'Typestring does not account for declared var-keywords',
					typestring, func, slf, func_class))
	if varg_count == 1:
		if argspec.varargs is None:
			raise TypeSyntaxError(_make_typestring_err_msg(
					'Typestring lists var-length args not declared in %s header.'
					% ('method' if slf else 'function'),
					typestring, func, slf, func_class))
	elif varg_count == 0:
		if not argspec.varargs is None:
			raise TypeSyntaxError(_make_typestring_err_msg(
					'Typestring does not account for declared var-length args',
					typestring, func, slf, func_class))
	return argString

def _funcsigtypesfromstring(typestring, argTypes = None, argspec = None, globals = globals(),
		selfType = None, argCount = None, unspecified_type = Any, defaults = None, func = None,
		slf = False, func_class = None):
	splt = typestring.find('->')
	if splt == -1:
		return None
	argString = typestring[:splt].strip()
	if not argspec is None:
		argString = _check_vararg_typestring(typestring, argString, argspec, func, slf, func_class)
	if _isargsellipsis(argString):
# 		useEllipsis = True
		if not argTypes is None:
			argString = ''.join(('(', ', '.join(['Any' if x is None else x for x in argTypes]), ')'))
# 	else:
# 		useEllipsis = False
	argTypes0 = argTypes
	resString = typestring[splt+2:].strip()
	argTp = eval(argString, globals)
	if selfType is None:
		argTypes = []
	else:
		argTypes = [selfType]
	try:
		argTypes += argTp
	except TypeError:
		argTypes.append(argTp)
	uc = 0
	if not argCount is None:
		if argTypes0 is None or len(argTypes0) <= len(argTypes):
			while len(argTypes) < argCount:
				argTypes.append(unspecified_type)
				uc += 1
		else:
			while len(argTypes) < argCount:
				if len(argTypes) < len(argTypes0):
					argTypes.append(argTypes0[len(argTypes)])
				else:
					argTypes.append(unspecified_type)
					uc += 1
	if not defaults is None:
		if len(defaults) < uc:
			uc = len(defaults)
		for i in range(uc):
			argTypes[-1-i] = pytypes.deep_type(defaults[-1-i])
	# Note: Tuple constructor automatically normalizes None to NoneType
	tpl = pytypes.make_Tuple(tuple(argTypes))
# 	if useEllipsis:
# 		tpl.__tuple_use_ellipsis__ = True

	# Normalize occurrence of None to type(None).
	# (Doing this in pre-eval manner/text-mode is easier than going
	#  through maybe nested type-vars, etc)
	# To avoid that this creates type(type(None)) if type(None) is already in place:
	resString = resString.replace('type(None)', 'None')
	resString = resString.replace('None', 'type(None)')	

	resType = eval(resString, globals)
	return tpl, resType
