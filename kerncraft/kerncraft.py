#!/usr/bin/env python

from __future__ import print_function

import argparse
import ast
import sys
import os.path
import pickle
import shutil
import math
import re
import pprint
import itertools

import models
from kernel import Kernel
from machinemodel import MachineModel


def space(start, stop, num, endpoint=True, log=False, base=10):
    '''
    Returns list of evenly spaced integers over an interval.

    Numbers can either be evenlty distributed in a linear space (if *log* is False) or in a log 
    space (if *log* is True). If *log* is True, base is used to define the log space basis.
    
    If *endpoint* is True, *stop* will be the last retruned value, as long as *num* >= 2.
    '''
    assert type(start) is int and type(stop) is int and type(num) is int, \
        "start, stop and num need to be intergers"
    assert num >= 2, "num has to be atleast 2"
    
    if log:
        start = math.log(start, base)
        stop = math.log(stop, base)
    
    if endpoint:
        steplength = float((stop-start))/float(num-1)
    else:
        steplength = float((stop-start))/float(num)
    
    i = 0
    while i < num:
        if log:
            yield int(round(base**(start + i*steplength)))
        else:
            yield int(round(start + i*steplength))
        i += 1
    

class AppendStringRange(argparse.Action):
    """
    Action to append a string and a range discription
    
    A range discription must have the following format: start[-stop[:num[log[base]]]]
    if stop is given, a list of integers is compiled
    if num is given, an evently spaced lsit of intergers from start to stop is compiled
    if log is given, the integers are evenly spaced on a log space
    if base is given, the integers are evently spaced on that base (default: 10)
    """
    def __call__(self, parser, namespace, values, option_string=None):
        message = ''
        if len(values) != 2:
            message = 'requires 2 arguments'
        else:
            m = re.match(r'(?P<start>\d+)(?:-(?P<stop>\d+)(?::(?P<num>\d+)'
                         r'(:?(?P<log>log)(:?(?P<base>\d+))?)?)?)?',
                         values[1])
            if m:
                gd = m.groupdict()
                if gd['stop'] is None:
                    values[1] = [int(gd['start'])]
                elif gd['num'] is None:
                    values[1] = range(int(gd['start']), int(gd['stop'])+1)
                else:
                    log = gd['log'] is not None
                    base = int(gd['base']) if gd['base'] is not None else 10
                    values[1] = space(
                        int(gd['start']), int(gd['stop']), int(gd['num']), log=log, base=base)
            else:
                message = 'second argument must match: start[-stop[:num[log[base]]]]'

        if message:
            raise argparse.ArgumentError(self, message)

        if hasattr(namespace, self.dest):
            getattr(namespace, self.dest).append(values)
        else:
            setattr(namespace, self.dest, [values])


def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--machine', '-m', type=file, required=True,
                        help='Path to machine description yaml file.')
    parser.add_argument('--pmodel', '-p', choices=models.__all__, required=True, action='append',
                        default=[], help='Performance model to apply')
    parser.add_argument('-D', '--define', nargs=2, metavar=('KEY', 'VALUE'), default=[],
                        action=AppendStringRange,
                        help='Define constant to be used in C code. Values must be integer or '
                             'match start-stop[:num[log[base]]]. If range is given, all '
                             'permutation s will be tested. Overwrites constants from testcase '                                 'file.')
    parser.add_argument('--verbose', '-v', action='count',
                        help='Increases verbosity level.')
    parser.add_argument('code_file', metavar='FILE', type=argparse.FileType(),
                        help='File with loop kernel C code')
    parser.add_argument('--asm-block', metavar='BLOCK', default='auto',
                        help='Number of ASM block to mark for IACA, "auto" for automatic '
                             'selection or "manual" for interactiv selection.')
    parser.add_argument('--store', metavar='PICKLE', type=argparse.FileType('a+b'),
                        help='Addes results to PICKLE file for later processing.')
    parser.add_argument('--unit', '-u', choices=['cy/CL', 'It/s', 'FLOP/s'],
                        help='Select the output unit, defaults to model specific if not given.')
    parser.add_argument('--cores', '-c', metavar='CORES', type=int, default=1,
                        help='Number of cores to be used in parallel. (default: 1)')
    parser.add_argument('--latency', action='store_true',
                        help='Use pessimistic IACA latency instead of throughput prediction.')
    
    for m in models.__all__:
        ag = parser.add_argument_group('arguments for '+m+' model', getattr(models, m).name)
        getattr(models, m).configure_arggroup(ag)
    
    return parser

def check_arguments(args):
    if args.asm_block not in ['auto', 'manual']:
        try:
            args.asm_block = int(args.asm_block)
        except ValueError:
            parser.error('--asm-block can only be "auto", "manual" or an integer')

def run(parser, args, output_file=sys.stdout):
    # Try loading results file (if requested)
    result_storage = {}
    if args.store:
        args.store.seek(0)
        try:
            result_storage = pickle.load(args.store)
        except EOFError:
            pass
        args.store.close()
    
    # machine information
    # Read machine description
    machine = MachineModel(args.machine.name)

    # process kernel
    code = args.code_file.read()
    kernel = Kernel(code, filename=args.code_file.name)

    # build defines permutations
    define_dict = {}
    for name, values in args.define:
        if name not in define_dict:
            define_dict[name] = [[name, v] for v in values]
            continue
        for v in values:
            if v not in define_dict[name]:
                define_dict[name].append([name, v])
    define_product = list(itertools.product(*define_dict.values()))

    for define in define_product:
        # Add constants from define arguments
        for k, v in define:
            kernel.set_constant(k, v)

        kernel.process()

        for model_name in set(args.pmodel):
            # print header
            print('{:=^80}'.format(' kerncraft '), file=output_file)
            print('{:<40}{:>40}'.format(args.code_file.name, '-m '+args.machine.name),
                  file=output_file)
            print(' '.join(['-D {} {}'.format(k,v) for k,v in define]), file=output_file)
            print('{:-^80}'.format(' '+model_name+' '), file=output_file)
            
            if args.verbose > 1:
                kernel.print_kernel_code()
                print(file=output_file)
                kernel.print_variables_info()
                kernel.print_kernel_info()
            if args.verbose > 0:
                kernel.print_constants_info()
            
            model = getattr(models, model_name)(kernel, machine, args, parser)

            model.analyze()
            model.report(output_file=output_file)
            
            # Add results to storage
            kernel_name = os.path.split(args.code_file.name)[1]
            if kernel_name not in result_storage:
                result_storage[kernel_name] = {}
            if tuple(kernel._constants.items()) not in result_storage[kernel_name]:
                result_storage[kernel_name][tuple(kernel._constants.items())] = {}
            result_storage[kernel_name][tuple(kernel._constants.items())][model_name] = \
                model.results
            
            print(file=output_file)
        
        # Save storage to file (if requested)
        if args.store:
            tempname = args.store.name + '.tmp'
            with open(tempname, 'w+') as f:
                pickle.dump(result_storage, f)
            shutil.move(tempname, args.store.name)

def main():
    # Create and populate parser
    parser = create_parser()
    
    # Parse given arguments
    args = parser.parse_args()
    
    # Checking arguments
    check_arguments(args)
    
    # BUSINESS LOGIC IS FOLLOWING
    run(parser, args)

if __name__ == '__main__':
    main()
