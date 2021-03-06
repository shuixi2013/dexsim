import logging
import re
from time import clock

from smaliemu.emulator import Emulator
from timeout3 import TIMEOUT_EXCEPTION

from ..plugin import Plugin
from dexsim import DEBUG

PLUGIN_CLASS_NAME = "TEMPLET_PLUS"


logger = logging.getLogger(__name__)


android_strs = [
    'Ljava/lang/System;', 'Landroid/os/Environment', 'Ljava/lang/String;->'
]

class TEMPLET_PLUS(Plugin):
    '''
    自动匹配解密插件

    用于匹配那些比较麻烦的解密方法，相对比较耗时——有时候，会超级耗时
    '''
    name = "TEMPLET_PLUS"
    enabled = False
    tname = None
    index = 3
    ONE_TIME = False

    # 存放字段相关数据，类、字段名；APK根据其获取对应的值
    feild_datas = {}

    def __init__(self, driver, smalidir):
        Plugin.__init__(self, driver, smalidir)
        self.emu2 = Emulator()

        # 匹配参数为内置类型的静态调用函数
        INVOKE_STATIC_RE = (
            r'invoke-static.*?{([(v|p)\.\d,\s]*)}, (.*?);->(.*?)'
            r'\(((?:B|S|C|I|J|F|D|Ljava/lang/String;|'
            r'\[B|\[S|\[C|\[I|\[J|\[F|\[D|\[Ljava/lang/String;'
            r')*?)\)Ljava/lang/String;')

        # 匹配proto
        PROTO_RE = (
            r'(B|S|C|I|J|F|D|Ljava/lang/String;|'
            r'\[B|\[S|\[C|\[I|\[J|\[F|\[D|\[Ljava/lang/String;)'
        )

        self.invoke_static_ptn = re.compile(INVOKE_STATIC_RE)
        self.proto_ptn = re.compile(PROTO_RE)
        self.arr_data_ptn = re.compile(self.ARRAY_DATA_PATTERN)
        self.move_result_obj_ptn = re.compile(self.MOVE_RESULT_OBJECT)

    def run(self):
        if self.ONE_TIME:
            return
        print('Run ' + __name__, end=' ', flush=True)
        # logger.info('running')
        self.proccess()
        self.ONE_TIME = True

    def proccess(self):
        for sf in self.smalidir:
            for mtd in sf.get_methods():
                if 'Lcom/cmcc/papp/a/a;->b(Context, String)V' not in str(mtd):
                    continue

                if DEBUG:
                    from colorclass.color import Color
                    print()
                    print(Color.red(str(mtd)))
                
                self._process_mtd(mtd)
            self.optimize()
            self.clear()
   
    def get_field_value(self, json_item):
        """
        把Field的值，写回到smali中

        因为Field本来就是唯一，所以，不需要ID，一些繁琐的东西。
        """
        print('准备动态获取Field的值', json_item)
        if not self.feild_datas:
            return

        from json import JSONEncoder
        import tempfile

        jsons = JSONEncoder().encode(self.feild_datas)
        self.feild_datas = {}

        outputs = {}
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tfile:
            tfile.write(jsons)
        outputs = self.driver.decode(tfile.name)
        import os
        os.unlink(tfile.name)
        
        if not outputs:
            return False
        
        print(outputs)
        cname = json_item['className']
        fname = json_item['fieldName'][0]

        value = outputs[cname][fname]
        print(value)
        if json_item['fieldType'].startswith('['):
            import ast
            return ast.literal_eval(value)

        return value


    def _process_mtd(self, mtd):
        # 如果存在数组
        array_data_content = []
        arr_res = self.arr_data_ptn.search(mtd.get_body())
        if arr_res:
            array_data_content = re.split(r'\n\s', arr_res.group())

        lines = re.split(r'\n\s*', mtd.get_body())

        old_body = lines.copy()  # 存放原始方法体
        new_body = []   # 存放解密后的方法体

        snippet = []  # 存放smali代码，用于模拟执行
        args = self.fields   # 存放方法参数，用于smaliemu执行

        index = -1  # 用于计数

        for line in lines:
            snippet.append(line)
            index += 1

            if 'sget' in line:
                print('->', line)
                from smafile import SmaliLine
                result = SmaliLine.parse(line)
                if not result:
                    continue
                cname, fname, rtype, rname = result

                if rtype not in {'I', 'S', 'C', 'F', 'Ljava/lang/String;', '[B', '[C', '[I', '[Ljava/lang/String;'}:
                    continue

                abs_fname = self.java2smali(cname) + '->' + fname + ':' + rtype
                
                if abs_fname in self.fields.keys():
                    continue

                if abs_fname in args.keys():
                    print(args[abs_fname])
                    continue

                print(cname, fname, rtype, rname)
                self.feild_datas = {
                    'type': 'field',
                    'data': []
                }
                json_item = {
                    'className': cname,
                    'fieldName': [fname],
                    'fieldType': rtype
                }
                print(json_item)
                self.feild_datas['data'].append(json_item)
                
                value = self.get_field_value(json_item)
                if not value:
                    continue
                
                self.fields[abs_fname] = value
                continue

            if 'invoke-static' not in line or not line.endswith(')Ljava/lang/String;'):
                new_body.append(line)
                continue

            # 排除Android自身的类
            flag = False
            for clz in android_strs:
                if clz in line:
                    flag = True
                    break
            if flag:
                new_body.append(line)
                continue
            
            # result = self.invoke_static_ptn.match(line)
            # if not result:
            #     new_body.append(line)
            #     continue
            
            # print(result)

            from smafile import SmaliLine
            cname, mname, protos, rtype, rnames = SmaliLine.parse_invoke_static(line)
            
            # 参数名(寄存器的名)，类名，方法名，proto(简称)
            # register_name, class_name, mtd_name, protos
            # ('v1, v2, v3', 'Lcom/game/pay/sdk/y', 'a', 'ISB')
            # 解密参数的寄存器名
            # rnames = result.groups()[0].split(', ')
            # cname = result.groups()[1][1:].replace('/', '.')
            # mname = result.groups()[2]
            # protos = self.proto_ptn.findall(result.groups()[3])

            print(rnames, cname, mname, protos)

            # 初始化所有寄存器
            del snippet[-1]
            # snippet.extend(array_data_content)
            # try:
            #     args.update(self.pre_process(snippet))
            # except TIMEOUT_EXCEPTION:
            #     pass
            
            try:
                # registers = self.get_vm_variables(
                #     snippet, args, rnames)
                # args = registers if registers else args
                if DEBUG:
                    print('smali代码：')
                    print(snippet)
                self.emu.call(snippet, args=args, cv=True, thrown=False)
                registers = self.emu.vm.variables
                if registers:
                    for k, v in registers.items():
                        if v is None:
                            continue
                        args[k] = v
                
                registers = args

            except TIMEOUT_EXCEPTION:
                snippet.clear()
                # new_body.append(line)
                continue

            snippet.clear()
            if not registers:
                continue
            # snippet.clear()

            # if not registers:
            #     new_body.append(line)
            #     continue

            # 从寄存器中获取对应的参数
            # 参数获取 "arguments": ["I:198", "I:115", "I:26"]}
            arguments = []
            # args = {}  # the parameter of smali method
            ridx = -1
            for item in protos:
                ridx += 1
                rname = rnames[ridx]
                if rname not in registers:
                    break
                value = registers[rnames[ridx]]
                argument = self.convert_args(item, value)
                if argument is None:
                    break
                arguments.append(argument)
            
            if DEBUG:
                print('解密参数:')
                print(arguments)

            if len(arguments) != len(protos):
                new_body.append(line)
                continue

            json_item = self.get_json_item(cname, mname,
                                           arguments)
            # {id}_{rtn_name} 让这个唯一化，便于替换
            old_content = '# %s' % json_item['id']

            # 如果 move_result_obj 操作存在的话，解密后一起替换
            find = self.move_result_obj_ptn.search(lines[index + 1])

            if find:
                rtn_name = find.groups()[0]
                # 为了避免 '# abc_v10' 替换成 '# abc_v1'
                old_content = old_content + '_' + rtn_name + 'X'
                self.append_json_item(json_item, mtd, old_content,
                                      rtn_name)
            else:
                old_content = old_content + '_X'
                self.append_json_item(
                    json_item, mtd, old_content, None)
            old_body[index] = old_content

        mtd.set_body('\n'.join(old_body))

        if DEBUG:
            print("解密方法内容：")
            print(self.json_list)
