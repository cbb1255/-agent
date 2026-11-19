"""5 BM25 关键词检索
中文要先分词再建索引，jieba 负责分词，rank_bm25 负责打分。
"""
import jieba
import pickle
import rank_bm25
import os
from app.rag.splitter import search_text
def tokenize(text: str) -> list[str]:
    """ze函数 tokeni
    入参 text: 待分词的文本（chunk 正文或用户查询）
    返回值: list[str]，分词结果，已去掉纯空白 token
    逻辑步骤:
     1. text.lower() 统一小写
     2. jieba.lcut(...) 切词
     3. 过滤掉 strip() 为空的 token
     4. 返回 token 列表
     作用就是小写后切分然后过滤
    """
    text_lower=text.lower()
    tokens=jieba.lcut(text_lower)
    chunk=[]
    for token in tokens:
        if token.strip():#去除空格后不为空
            chunk.append(token.strip())
    return chunk


class BM25Index:
    def __init__(self, chunks: list[dict]):
        """函数 BM25Index.__init__
        入参 chunks: chunk 列表，每个元素含 "chunk_id"、"text"、"metadata"
        返回值: None（构造实例）
        逻辑步骤:
         1. self.chunks = chunks 保存原文
         2. 对每个 chunk 的 text 调 tokenize，得到二维 token 列表 self.tokenized
         3. rank_bm25.BM25L(self.tokenized) 建索引，赋给 self.bm25
        作用就是先调用上一个函数切分然后追加到tokenized里最后调用rank_bm25排序
        """
        self.chunks=chunks
        self.tokenized=[]
        for chunk in chunks:
            text=search_text(chunk)
            tokens=tokenize(text)
            self.tokenized.append(tokens)
        # BM25L keeps matching terms positive even when the library has one or two notes.
        self.bm25=rank_bm25.BM25L(self.tokenized) if self.tokenized else None

    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """函数 BM25Index.search
        入参 query: 用户查询原文
        入参 top_k: 返回条数，默认 20
        返回值: list[dict]，每个元素是原 chunk 加上 "score" 与 "rank"，按 score 降序
         - score: BM25 分值
         - rank: 排名，从 1 开始（RRF 融合要用）
        逻辑步骤:
         1. tokenize(query) 得到查询词
         2. self.bm25.get_scores(...) 拿到每个 chunk 的分值
         3. 过滤掉 score <= 0 的项，按分值降序取前 top_k
         4. 写回 score 与 rank（从 1 开始）后返回
        先把问题切分然后取得score得分如果score>0取出对应的chunk追加到temp中然后降序去前top_result个结果
        保存元数据防止污染数据然后追加到finall结果后返回
        """
        if self.bm25 is None:# 没有索引就没有结果
            return []
        query_token=tokenize(query)
        scores=self.bm25.get_scores(query_token)
        temp_result=[]
        for i in range(len(scores)):
            score=scores[i]
            if score>0:
                chunk=self.chunks[i]# 属性名是 self.chunks，不是 self.chunk
                temp_result.append((score,chunk))
        temp_result.sort(key=lambda x:x[0],reverse=True)#lambda按照score排序reverse反转排序为降序
        top_result=temp_result[:top_k]
        finaly_result=[]
        rank=1
        for score,chunk in top_result:#已经排好序
            item=chunk.copy()#防止污染数据
            item["score"]=score
            item["rank"]=rank
            finaly_result.append(item)
            rank+=1
        return finaly_result

    def save(self, path: str):
        """函数 BM25Index.save
        入参 path: 落盘路径（pickle 文件，例如 storage/chroma/bm25.pkl）
        返回值: None
        逻辑步骤:
         1. 把 {"chunks": 原文列表, "tokens": 分词结果} 打包成 dict
         2. 父目录不存在先 mkdir(parents=True, exist_ok=True)
         3. pickle.dump 写入 path（只存语料与分词，加载后再重建 BM25Okapi）
        作用就是先把data弄成一个字典里面有chunk和token然后看是否有目录保存如果没有就迭代创建然后打开创捷的或者是已有的文件然后写入"""
        data={
            "chunks": self.chunks,
            "tokens": self.tokenized
        }#{"chunks": 原文列表, "tokens": 分词结果} 打包成 dict
        dir_name=os.path.dirname(path)#os.path.dirname(path)：获取 path 的目录部分
        if dir_name:#防止 path 只是一个文件名（例如 bm25.pkl）
            os.makedirs(dir_name,exist_ok=True)#会递归创建目录。如果 data 目录不存在，它会创建
            #data exist_ok=True：这是一个关键参数。如果目标目录已经存在，
            #os.makedirs 不会抛出 FileExistsError，而是直接跳过
        with open(path,"wb") as f:#以二进制写入模式 ("wb") 打开文件
            pickle.dump(data,f)#将之前创建的 data 字典序列化并写入到文件对象 f 中
    @classmethod#这是一个类方法 (@classmethod)，它负责从指定的文件路径加载数据，并重建一个 BM25Index 实例
    def load(cls, path: str):
        """函数 BM25Index.load
        入参 path: save() 写出的 pickle 路径
        返回值: BM25Index 实例
        逻辑步骤:
         1. pickle.load 读出 dict，取 chunks
         2. 用 chunks 构造实例（pickle 里有 tokens 就直接复用，避免重复切词）并重建 BM25Okapi
         3. 返回实例
        作用就是打开文件然后取出对应的chunk和token然后返回obj"""
        with open(path,"rb") as f:#以二进制读取模式 ("rb") 打开文件。
            data=pickle.load(f)#从文件对象 f 中读取序列化的数据，并将其反序列化回 Python 对象（在这里是之前保存的 data 字典）。
        obj=cls.__new__(cls)
        obj.chunks=data["chunks"]#将加载的原始文本块赋值给新实例的chunks属性
        obj.tokenized=data["tokens"]#将加载的分词结果赋值给新实例的tokenized属性
        obj.bm25=rank_bm25.BM25L(obj.tokenized) if obj.tokenized else None
        return obj
