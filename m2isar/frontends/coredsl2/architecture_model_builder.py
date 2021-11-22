import inspect
import itertools
import logging
from typing import List, Mapping, Set, Tuple, Union

from ...metamodel import arch, behav
from . import expr_interpreter
from .parser_gen import CoreDSL2Lexer, CoreDSL2Parser, CoreDSL2Visitor

logger = logging.getLogger("arch_builder")

def patch_model():
	"""Monkey patch transformation functions inside instruction_transform
	into model_classes.behav classes
	"""

	for name, fn in inspect.getmembers(expr_interpreter, inspect.isfunction):
		sig = inspect.signature(fn)
		param = sig.parameters.get("self")
		if not param:
			logger.warning("no self parameter found in %s", fn)
			continue
		if not param.annotation:
			logger.warning("self parameter not annotated correctly for %s", fn)
			continue

		logger.debug("patching %s with fn %s", param.annotation, fn)
		param.annotation.generate = fn

patch_model()


RADIX = {
	'b': 2,
	'h': 16,
	'd': 10,
	'o': 8
}

SHORTHANDS = {
	"char": 8,
	"short": 16,
	"int": 32,
	"long": 64
}

SIGNEDNESS = {
	"signed": True,
	"unsigned": False
}

def flatten_list(l: list):
	ret = []
	for item in l:
		if isinstance(item, list):
			ret += flatten_list(item)
		else:
			ret.append(item)
	return ret

class ArchitectureModelBuilder(CoreDSL2Visitor):
	_constants: Mapping[str, arch.Constant]
	_instructions: Mapping[str, arch.Instruction]
	_functions: Mapping[str, arch.Function]
	_instruction_sets: Mapping[str, arch.InstructionSet]
	_read_types: Mapping[str, str]
	_memories: Mapping[str, arch.Memory]
	_memory_aliases: Mapping[str, arch.Memory]
	_overwritten_instrs: List[Tuple[arch.Instruction, arch.Instruction]]
	_instr_classes: Set[int]
	_main_reg_file: Union[arch.Memory, None]

	def __init__(self):
		super().__init__()
		self._constants = {}
		self._instructions = {}
		self._functions = {}
		self._instruction_sets = {}
		self._read_types = {}
		self._memories = {}
		self._memory_aliases = {}

		self._overwritten_instrs = []
		self._instr_classes = set()
		self._main_reg_file = None

	def visitBit_field(self, ctx: CoreDSL2Parser.Bit_fieldContext):
		left = self.visit(ctx.left)
		right = self.visit(ctx.right)
		range = arch.RangeSpec(left.value, right.value)
		return arch.BitField(ctx.name.text, range, arch.DataType.U)

	def visitBit_value(self, ctx: CoreDSL2Parser.Bit_valueContext):
		val = self.visit(ctx.value)
		return arch.BitVal(val.bit_size, val.value)

	def visitInstruction_set(self, ctx: CoreDSL2Parser.Instruction_setContext):
		self._read_types[ctx.name.text] = None

		name = ctx.name.text
		extension = []
		if ctx.extension:
			extension = [obj.text for obj in ctx.extension]

		contents = flatten_list([self.visit(obj) for obj in ctx.sections])

		constants = {}
		memories = {}
		functions = {}
		instructions = {}

		for item in contents:
			if isinstance(item, arch.Constant):
				constants[item.name] = item
			elif isinstance(item, arch.Memory):
				memories[item.name] = item
			elif isinstance(item, arch.Function):
				functions[item.name] = item
			elif isinstance(item, arch.Instruction):
				instructions[(item.code, item.mask)] = item
			else:
				raise ValueError("unexpected item encountered")

		i = arch.InstructionSet(name, extension, constants, memories, functions, instructions)

		if name in self._instruction_sets:
			raise ValueError(f"instruction set {name} already defined")

		self._instruction_sets[name] = i
		return i

	def visitCore_def(self, ctx: CoreDSL2Parser.Core_defContext):
		self.visitChildren(ctx)

		name = ctx.name.text

		c = arch.CoreDef(name, list(self._read_types.keys()), None,
			self._constants, self._memories, self._memory_aliases,
			self._functions, self._instructions, self._instr_classes,
			self._main_reg_file)

		return c

	def visitSection_arch_state(self, ctx: CoreDSL2Parser.Section_arch_stateContext):
		decls = [self.visit(obj) for obj in ctx.declarations]
		decls = list(itertools.chain.from_iterable(decls))
		for obj in ctx.expressions:
			self.visit(obj)

		return decls

	def visitInstruction(self, ctx: CoreDSL2Parser.InstructionContext):
		encoding = [self.visit(obj) for obj in ctx.encoding]
		attributes = [self.visit(obj) for obj in ctx.attributes]
		disass = ctx.disass.text if ctx.disass is not None else None

		i = arch.Instruction(ctx.name.text, attributes, encoding, disass, ctx.behavior)
		self._instr_classes.add(i.size)

		instr_id = (i.code, i.mask)

		if instr_id in self._instructions:
			self._overwritten_instrs.append((self._instructions[instr_id], i))

		self._instructions[instr_id] = i

		return i

	def visitFunction_definition(self, ctx: CoreDSL2Parser.Function_definitionContext):
		attributes = [self.visit(obj) for obj in ctx.attributes]
		type_ = self.visit(ctx.type_)

		params = []
		if ctx.params:
			params = self.visit(ctx.params)

		if not isinstance(params, list):
			params = [params]

		return_size = None
		data_type = arch.DataType.NONE

		if isinstance(type_, arch.IntegerType):
			return_size = type_._width
			data_type = arch.DataType.S if type_.signed else arch.DataType.U

		f = arch.Function(ctx.name.text, return_size, data_type, params, ctx.behavior)

		if ctx.name.text in self._functions:
			raise ValueError(f"function {ctx.name.text} already defined")
		self._functions[ctx.name.text] = f
		return f

	def visitParameter_declaration(self, ctx: CoreDSL2Parser.Parameter_declarationContext):
		type_ = self.visit(ctx.type_)
		name = None
		size = None
		if ctx.dd:
			if ctx.dd.name:
				name = ctx.dd.name.text
			if ctx.dd.size:
				size = [self.visit(obj) for obj in ctx.dd.size]

		p = arch.FnParam(name, type_._width, arch.DataType.S if type_.signed else arch.DataType.U)
		return p

	def visitInteger_constant(self, ctx: CoreDSL2Parser.Integer_constantContext):
		text: str = ctx.value.text.lower()

		tick_pos = text.find("'")

		if tick_pos != -1:
			width = int(text[:tick_pos])
			radix = text[tick_pos+1]
			value = int(text[tick_pos+2:], RADIX[radix])

		else:
			value = int(text, 0)
			if text.startswith("0b"):
				width = len(text) - 2
			elif text.startswith("0x"):
				width = (len(text) - 2) * 4
			elif text.startswith("0") and len(text) > 1:
				width = (len(text) - 1) * 3
			else:
				width = value.bit_length()

		return behav.IntLiteral(value, width)

	def visitDeclaration(self, ctx: CoreDSL2Parser.DeclarationContext):
		storage = [self.visit(obj) for obj in ctx.storage]
		qualifiers = [self.visit(obj) for obj in ctx.qualifiers]
		attributes = [self.visit(obj) for obj in ctx.attributes]

		type_ = self.visit(ctx.type_)

		decls: List[CoreDSL2Parser.Init_declaratorContext] = ctx.init

		ret_decls = []

		for decl in decls:
			name = decl.declarator.name.text

			if type_.ptr == "&": # register alias
				size = [1]
				init: behav.IndexedReference = self.visit(decl.init)
				attributes = []

				if decl.declarator.size:
					size = [self.visit(obj).value for obj in decl.declarator.size]

				left = init.index
				right = init.right if init.right is not None else left
				reference = init.reference

				if decl.attributes:
					attributes = [self.visit(obj) for obj in decl.attributes]

				range = arch.RangeSpec(left, right)

				#if range.length != size[0]:
				#	raise ValueError(f"range mismatch for {name}")

				m = arch.Memory(name, range, type_._width, attributes)
				m.parent = reference
				m.parent.children.append(m)

				if name in self._memory_aliases:
					raise ValueError(f"memory {name} already defined")

				self._memory_aliases[name] = m
				ret_decls.append(m)

			else:
				if len(storage) == 0: # no storage specifier -> implementation parameter, "Constant" in M2-ISA-R
					init = None
					if decl.init is not None:
						init = self.visit(decl.init)

					c = arch.Constant(name, init, [])

					if name in self._constants:
						raise ValueError(f"constant {name} already defined")
					self._constants[name] = c
					ret_decls.append(c)

				elif "register" in storage or "extern" in storage:
					size = [1]
					init = 0
					attributes = []

					if decl.declarator.size:
						size = [self.visit(obj) for obj in decl.declarator.size]

					if decl.init is not None:
						init = self.visit(decl.init)

					if decl.attributes:
						attributes = [self.visit(obj) for obj in decl.attributes]

					range = arch.RangeSpec(size[0])
					m = arch.Memory(name, range, type_._width, attributes)

					if name in self._memories:
						raise ValueError(f"memory {name} already defined")

					if arch.MemoryAttribute.IS_MAIN_REG in attributes:
						self._main_reg_file = m

					self._memories[name] = m
					ret_decls.append(m)

		return ret_decls

	def visitType_specifier(self, ctx: CoreDSL2Parser.Type_specifierContext):
		type_ = self.visit(ctx.type_)
		if ctx.ptr:
			type_.ptr = ctx.ptr.text
		return type_

	def visitInteger_type(self, ctx: CoreDSL2Parser.Integer_typeContext):
		signed = True
		width = None

		if ctx.signed is not None:
			signed = self.visit(ctx.signed)

		if ctx.size is not None:
			width = self.visit(ctx.size)

		if ctx.shorthand is not None:
			width = self.visit(ctx.shorthand)

		if isinstance(width, behav.IntLiteral):
			width = width.value
		elif isinstance(width, behav.NamedReference):
			width = width.reference
		else:
			raise ValueError("width has wrong type")

		return arch.IntegerType(width, signed, None)

	def visitVoid_type(self, ctx: CoreDSL2Parser.Void_typeContext):
		return arch.VoidType(None)

	def visitBool_type(self, ctx: CoreDSL2Parser.Bool_typeContext):
		return arch.IntegerType(1, False, None)

	def visitBinary_expression(self, ctx: CoreDSL2Parser.Binary_expressionContext):
		left = self.visit(ctx.left)
		right = self.visit(ctx.right)
		op = ctx.bop.text
		return behav.BinaryOperation(left, op, right)

	def visitSlice_expression(self, ctx: CoreDSL2Parser.Slice_expressionContext):
		left = self.visit(ctx.left)
		right = self.visit(ctx.right) if ctx.right is not None else None
		expr = self.visit(ctx.expr).reference

		op = behav.IndexedReference(expr, left, right)
		return op

	def visitPrefix_expression(self, ctx: CoreDSL2Parser.Prefix_expressionContext):
		prefix = ctx.prefix.text
		expr = self.visit(ctx.right)
		return behav.UnaryOperation(prefix, expr)

	def visitReference_expression(self, ctx: CoreDSL2Parser.Reference_expressionContext):
		name = ctx.ref.text
		ref = self._constants.get(name) or self._memories.get(name) or self._memory_aliases.get(name)
		if ref is None:
			raise ValueError(f"reference {name} could not be resolved")
		return behav.NamedReference(ref)

	def visitStorage_class_specifier(self, ctx: CoreDSL2Parser.Storage_class_specifierContext):
		return ctx.children[0].symbol.text

	def visitInteger_signedness(self, ctx: CoreDSL2Parser.Integer_signednessContext):
		return SIGNEDNESS[ctx.children[0].symbol.text]

	def visitInteger_shorthand(self, ctx: CoreDSL2Parser.Integer_shorthandContext):
		return behav.IntLiteral(SHORTHANDS[ctx.children[0].symbol.text])

	def visitAssignment_expression(self, ctx: CoreDSL2Parser.Assignment_expressionContext):
		left = self.visit(ctx.left)
		right = self.visit(ctx.right)

		if isinstance(left, behav.NamedReference):
			if isinstance(left.reference, arch.Constant):
				left.reference.value = right.generate(None)

			elif isinstance(left.reference, arch.Memory):
				left.reference._initval[None] = right.generate(None)

		elif isinstance(left, behav.IndexedReference):
			left.reference._initval[left.index.generate(None)] = right.generate(None)

	def visitTerminal(self, node):
		if node.symbol.type == CoreDSL2Lexer.MEM_ATTRIBUTE:
			return arch.MemoryAttribute[node.symbol.text.upper()]
		elif node.symbol.type == CoreDSL2Lexer.INSTR_ATTRIBUTE:
			return arch.InstrAttribute[node.symbol.text.upper()]
		return super().visitTerminal(node)

	def visitChildren(self, node):
		ret = super().visitChildren(node)
		if isinstance(ret, list) and len(ret) == 1:
			return ret[0]
		return ret

	def aggregateResult(self, aggregate, nextResult):
		ret = aggregate
		if nextResult is not None:
			if ret is None:
				ret = [nextResult]
			else:
				ret += [nextResult]
		return ret