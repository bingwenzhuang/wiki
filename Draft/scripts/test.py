#!/usr/bin/env python

def binary_search(alist,item):
    n = len(alist)
    mid = n//2
    if n > 0:
        if alist[mid] == item:
            return True
        elif alist[mid] > item:
            return binary_search(alist[:mid],item)
        elif alist[mid] < item:
            return binary_search(alist[mid+1:],item)
        return False

# 必须有序的数组
li = [17,20,26,31,44,54,55,77,93]   
print(binary_search(li,21))

class Node(object):
    def __init__(self,item):
        self.elem = item
        self.lchild = None
        self.rchild = None

class Tree(object):
    def __init__(self):
        self.root = None
    def add(self,item):
        node = Node(item)
        if self.root is None:
            self.root = node
            return
        queue = [self.root]
        while queue:
            cur_node = queue.pop(0)
            
