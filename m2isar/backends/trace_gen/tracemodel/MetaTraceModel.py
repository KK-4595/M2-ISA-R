# 
# Copyright 2022 Chair of EDA, Technical University of Munich
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#       http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import re

class MetaTraceModel_base:
    __isFrozen = False

    def __setattr__(self, key, value):
        if self.__isFrozen and not hasattr(self, key):
            raise TypeError("Attempting to add new attribute to frozen class %r" %self)
        object.__setattr__(self, key, value)

    def __init__(self):
        self.__isFrozen = True

class Trace(MetaTraceModel_base):

    def __init__(self, name_):
        self.name = name_
        self.instructionTypes = []
        self.traceValues = {}
        self.separator = "|"
        
        super().__init__()

    def createAndAddTraceValue(self, name_, type_="int", size_=-1):
        trVal = TraceValue(name_, type_, size_)
        self.traceValues[name_] = trVal
        return trVal
        
    def createAndAddInstructionType(self, name_, id_):
        instrType = InstructionType(name_, id_, self)
        self.instructionTypes.append(instrType)
        return instrType

    def getAllTraceValues(self):
        return self.traceValues.values()

    def getAllInstructionTypes(self):
        return self.instructionTypes

    def getAllMappings(self):
        mappings = []
        for instrType_i in self.getAllInstructionTypes():
            mappings.extend(instrType_i.getAllMappings())
        return mappings

    def getAllDescriptions(self):
        descriptions = []
        for map_i in self.getAllMappings():
            descriptions.append(map_i.description)
        return descriptions

    def setSeparator(self, sep_):
        self.separator = sep_

    def getSeparator(self):
        return self.separator
    
class InstructionType(MetaTraceModel_base):

    def __init__(self, name_, id_, parent_):
        self.name = name_
        self.identifier = id_
        self.instructions = []
        self.bitfields = []
        self.mappings = {}
        self.__parent = parent_
        
        super().__init__()

    def addInstruction(self, name_):
        self.instructions.append(name_)

    def createAndAddMapping(self, trValName_, description_, position_):

        # Look up trace-value in dict. of parent/trace-model
        try:
            trVal = self.__parent.traceValues[trValName_]
        except KeyError:
            raise TypeError("Mapping for instruction %s: Cannot create mapping for trace-value %s. Trace-value does not exist (Make sure to add all trace-values to the trace-model before creating mappings)" %(self.name, trValName_))

        mapping = Mapping(self, trVal, description_, position_)
        self.mappings[trValName_] = mapping
        return mapping

    def getAllInstructions(self):
        return self.instructions
    
    def getAllMappings(self):
        return self.mappings.values()

    def getMapping(self, trVal_):
        try:
            return self.mappings[trVal_.name]
        except KeyError:
            return None

    
class TraceValue(MetaTraceModel_base):

    def __init__(self, name_, type_, size_):
        self.name = name_
        self.dataType = type_
        self.size = size_
        
        super().__init__()

class Mapping(MetaTraceModel_base):

    def __init__(self, type_, trVal_, descr_, pos_):
        self.instructionType = type_
        self.traceValue = trVal_
        self.description = Description(self, descr_)
        if pos_ not in ["pre", "post"]:
            raise RuntimeError("Cannot create object of type MetaTraceModel::Mapping with position \"%s\"! Currently supported positions are \"pre\" and \"post\"" %pos_)
        self.position = pos_
        
        super().__init__()

    def positionIsPre(self):
        return (self.position == "pre")

    def positionIsPost(self):
        return (self.position == "post")
        
    def getTraceValue(self):
        return self.traceValue

    def getDescription(self):
        return self.description

    def getInstructionType(self):
        return self.instructionType

class Description(MetaTraceModel_base):

    def __init__(self, map_, orig_):
        self.mapping = map_
        self.original = orig_
        self.resolved = self.resolve_description(orig_)  # Resolved descriptions

    def createAndAppendDescription(self, content_):
        """
        Parse the provided string and append the resolved description object to self.resolved
        """
        parsed_description = self.parse_description_string(content_)
        self.resolved.append(parsed_description)

    def getAllDescriptions(self):
        """
        Returns the list of resolved descriptions.
        """
        return self.resolved

    def getInstructionType(self):
        """
        Return the instruction type from the mapping.
        """
        return self.mapping.getInstructionType()

    def resolve_description(self, desc_string):
        """
        Parses a string and creates a Description object based on specific conditions.
        Handles operations and recursively parses nested descriptions.
        """

        # Helper to recursively parse nested descriptions
        def recursive_parse(desc_string):
            return self.resolve_description(desc_string.strip())

        # Strip the outermost curly braces, if present
        if desc_string.startswith("{") and desc_string.endswith("}"):
            desc_string = desc_string[1:-1].strip()

        # Function to find the first operator outside of any braces
        def find_operator_outside_braces(s):
            depth = 0
            for i, char in enumerate(s):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                elif char in '+-*/' and depth == 0:  # Only consider operators outside braces
                    return i
            return -1

        # Look for an operator outside any nested braces
        op_index = find_operator_outside_braces(desc_string)
        if op_index != -1:
            desc1_string = desc_string[:op_index].strip()  # First operand
            operation = desc_string[op_index]              # Operation (+, -, *, /)
            desc2_string = desc_string[op_index + 1:].strip()  # Second operand

            # Recursively parse both operands
            desc1 = recursive_parse(desc1_string)
            desc2 = recursive_parse(desc2_string)

            # Create a description with type 'op' and the two nested descriptions
            return DescriptionNode(op=operation, nested_descriptions=[desc1, desc2])

        # 1. If it is just a number, it is a constant
        if re.fullmatch(r'\d+', desc_string):
            return DescriptionNode(const=int(desc_string))

        # 2. If it is $pc
        if desc_string == "$pc":
            return DescriptionNode(pc="pc")

        # 3. Match $reg{} with potential nested content
        reg_match = re.match(r'\$reg\{(.+)\}', desc_string)
        if reg_match:
            value = reg_match.group(1).strip()
            if value.startswith("$bitfield"):
                nested_description = recursive_parse(value)
                return DescriptionNode(reg="reg", nested_descriptions=[nested_description])
            return DescriptionNode(reg=value)

        # 4. Match $bitfield{}
        bf_match = re.match(r'\$bitfield\{(.+)\}', desc_string)
        if bf_match:
            value = bf_match.group(1).strip()
            if value not in self.mapping.instructionType.bitfields:
                self.mapping.instructionType.bitfields.append(value)
            return DescriptionNode(bf=value)
        
        if desc_string == "$ba":
            return DescriptionNode(code="ba")
        
        if desc_string == "$asm":
            return DescriptionNode(asm="instr.printASM(ba)")

        raise ValueError(f"Unrecognized string format: {desc_string}")

    def reconstruct_string_from_description(self, description):
        """
        Reconstructs the original string from the given DescriptionNode object.
        """
        # If it's a constant, return the constant value as a string
        if description.active_type == 'const':
            return str(description.active_value)
        
        # If it's a pc, return "$pc"
        if description.active_type == 'pc':
            return "$pc"
        
        # If it's a register, return the register string with or without nested descriptions
        if description.active_type == 'reg':
            if description.nested_descriptions:
                # If there are nested descriptions (like $reg{$bitfield{BF1}})
                nested_str = self.reconstruct_string_from_description(description.nested_descriptions[0])
                return f"$reg{{{nested_str}}}"
            else:
                return f"$reg{{{description.active_value}}}"
        
        # If it's a bitfield, return the bitfield string
        if description.active_type == 'bf':
            return f"$bitfield{{{description.active_value}}}"
        
        # If it's an operation, reconstruct both nested descriptions and combine them with the operator
        if description.active_type == 'op':
            left_str = self.reconstruct_string_from_description(description.nested_descriptions[0])
            right_str = self.reconstruct_string_from_description(description.nested_descriptions[1])
            return f"{{{left_str} {description.active_value} {right_str}}}"
        
        raise ValueError(f"Unknown active_type: {description.active_type}")


class DescriptionNode:
    """
    This is the new helper class used in place of DescriptionSnippet.
    Handles parsed descriptions like constants, pc, reg, bf, and operations.
    """
    def __init__(self, const=None, pc=None, reg=None, bf=None, op=None, code=None, asm=None, nested_descriptions=None):
        self.active_type = None
        self.active_value = None
        self.nested_descriptions = []

        if const is not None:
            self.active_type = 'const'
            self.active_value = const
        elif pc is not None:
            self.active_type = 'pc'
            self.active_value = pc
        elif reg is not None:
            self.active_type = 'reg'
            self.active_value = reg
            self.nested_descriptions = nested_descriptions or []
        elif bf is not None:
            self.active_type = 'bf'
            self.active_value = bf
        elif op is not None:
            self.active_type = 'op'
            self.active_value = op
            self.nested_descriptions = nested_descriptions or []
        elif code is not None:
            self.active_type = 'code'
            self.active_value = code
        elif asm is not None:
            self.active_type = 'asm'
            self.active_value = asm

    def __repr__(self):
        if self.nested_descriptions:
            nested_repr = ', '.join([repr(nd) for nd in self.nested_descriptions])
            return f"DescriptionNode(active_type={self.active_type}, active_value={self.active_value}, nested_descriptions=[{nested_repr}])"
        else:
            return f"DescriptionNode(active_type={self.active_type}, active_value={self.active_value})"  