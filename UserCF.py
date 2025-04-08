# coding=utf-8
import random
import math
import requests
from operator import itemgetter
from flask import Flask, jsonify
from flask_cors import CORS
import json




app = Flask(__name__)
CORS(app)  # 解决跨域问题

# 微信云数据库配置

WX_CONFIG = {
    'APPID': 'wxb76b501d47647553',
    'APPSECRET': '2fca98641c0e635b50af711d0aa8383e',
    'ENV_ID': 'cloud1-0gnecw7o985a82da'
}



class UserBasedCF:
    def __init__(self):
        self.n_sim_user = 20
        self.n_rec_movie = 10
        self.trainSet = {}
        self.testSet = {}
        self.user_sim_matrix = {}
        self.movie_count = 0
        self.movie_titles = {}



    def get_stable_access_token(self,appid, app_secret):

        url = "https://api.weixin.qq.com/cgi-bin/stable_token"
        payload = {
            "grant_type": "client_credential",
            "appid": appid,
            "secret": app_secret,
            "force_refresh": False  # 改为True可强制刷新
        }
        response = requests.post(url, json=payload)

        # 添加详细错误处理
        if response.status_code != 200:
            raise ConnectionError(f"接口连接失败: HTTP {response.status_code}")

        data = response.json()
        if "access_token" not in data:
            error_msg = f"获取稳定版Token失败: {data}"
            if data.get("errcode") == 40013:
                error_msg += "\n>> 可能原因：AppID与当前操作的环境不匹配"
            raise PermissionError(error_msg)

        return data['access_token']

    def query_cloud_db(self,access_token, env, collection_name, page_size=100):
        """ 强制使用 skip 分页（适用于无有序字段的情况） """
        all_data = []
        page = 0

        while True:
            query = f'db.collection("{collection_name}").skip({page * page_size}).limit({page_size}).get()'
            url = f"https://api.weixin.qq.com/tcb/databasequery?access_token={access_token}"
            payload = {"env": env, "query": query}
            response = requests.post(url, json=payload).json()

            if response.get("errcode") != 0:
                raise Exception(f"查询失败: {response}")

            current_data = response.get("data", [])
            if not current_data:
                break

            all_data.extend(current_data)
            page += 1

        return all_data





    def load_movie_titles(self):
        """从云数据库加载小说元数据"""
        access_token = self.get_stable_access_token(WX_CONFIG['APPID'],WX_CONFIG['APPSECRET'])
        result= self.query_cloud_db( access_token,WX_CONFIG['ENV_ID'],'books')


       # print("数据库",result)



        parsed_result = []
        for item in result:
            if isinstance(item, str):
                try:
                    parsed_item = json.loads(item)  # 解析字符串 -> 字典
                    parsed_result.append(parsed_item)
                except json.JSONDecodeError as e:
                    print(f"解析失败: {item}，错误: {str(e)}")
            else:
                parsed_result.append(item)  # 非字符串数据直接保留（如已是字典）
        """print("parsed_result 类型:", type(parsed_result))  # 预期是 list
        if len(parsed_result) > 0:
            print("parsed_result[0] 类型:", type(parsed_result[0]))  # 预期是 dict
            print("parsed_result[0] 内容:", parsed_result[0])  # 预期是字典，如 {'movieId': '1', 'title': '...'}"""


        self.movie_titles = {
            item['movieId']: item['title']
            for item in parsed_result
        }
        print('Get book-bookid success!')


    def get_dataset(self):
        """从云数据库加载用户评分数据"""
        access_token = self.get_stable_access_token(WX_CONFIG['APPID'], WX_CONFIG['APPSECRET'])
        result= self.query_cloud_db( access_token,WX_CONFIG['ENV_ID'],'User')
        parsed_result = []
        for item in result:
            if isinstance(item, str):
                try:
                    parsed_item = json.loads(item)  # 解析字符串 -> 字典
                    parsed_result.append(parsed_item)
                except json.JSONDecodeError as e:
                    print(f"解析失败: {item}，错误: {str(e)}")
            else:
                parsed_result.append(item)  # 非字符串数据直接保留（如已是字典）


        for item in parsed_result:
            user = str(item['userId'])
            movie = str(item['movieId'])
            rating = float(item['rating'])
            if random.random() < 0.75:
                self.trainSet.setdefault(user, {})
                self.trainSet[user][movie] = rating
            else:
                self.testSet.setdefault(user, {})
                self.testSet[user][movie] = rating
        print('Split trainingSet and testSet success!')

    # 以下方法保持原有逻辑不变（需将movie改为novel）
    def calc_user_sim(self):
        # 构建“电影-用户”倒排索引
        print('Building movie-user table ...')
        movie_user = {}
        for user, movies in self.trainSet.items():
            for movie in movies:
                if movie not in movie_user:
                    movie_user[movie] = set()
                movie_user[movie].add(user)
        print('Build movie-user table success!')

        self.movie_count = len(movie_user)

        print('Total movie number = %d' % self.movie_count)

        print('Build user co-rated movies matrix ...')
        # 计算了用户们共同观看的次数self.user_sim_matrix。user1，user2：1
        for movie, users in movie_user.items():
            for u in users:
                for v in users:
                    if u == v:
                        continue
                    self.user_sim_matrix.setdefault(u, {})
                    self.user_sim_matrix[u].setdefault(v, 0)
                    self.user_sim_matrix[u][v] += 1
        print('Build user co-rated movies matrix success!')

        # 计算相似性，将次数转为相似度还存在user_sim_matrix里
        print('Calculating user similarity matrix ...')
        for u, related_users in self.user_sim_matrix.items():
            for v, count in related_users.items():
                self.user_sim_matrix[u][v] = count / math.sqrt(len(self.trainSet[u]) * len(self.trainSet[v]))
        print('Calculate user similarity matrix success!')





    def recommend(self, user):
        K = self.n_sim_user  # 20
        N = self.n_rec_movie  # 10
        rank = {}
        watched_movies = self.trainSet[user]  # 应该是user评过分的电影

        # 遍历相似用户的电影评分
        for v, wuv in sorted(self.user_sim_matrix[user].items(), key=itemgetter(1), reverse=True)[
                      0:K]:  # v是与user相似的用户，wuv是相似度
            for movie in self.trainSet[v]:  # 遍历与user相似的用户v看过的电影
                if movie in watched_movies:
                    continue
                rank[movie] = rank.get(movie, 0) + wuv

        # 按权重排序并截断TopN,给出N本推荐书籍
        sorted_rank = sorted(rank.items(), key=itemgetter(1), reverse=True)[0:N]
        print("get sorted_rank success! ",sorted_rank)
        return sorted_rank

        # 映射电影ID到名称
        recommendations = []
        for movie_id, score in sorted_rank:
            title = self.movie_titles.get(movie_id, "Unknown Movie")
            recommendations.append((title, score))
        print("get recommendation success!")
        return recommendations  # 返回带名称的推荐列表

    def recommend_for_user(self, user_id):
        user_id = str(user_id)
        if user_id not in self.trainSet:
            return []
        recommendations = self.recommend(user_id)
        return [(self.movie_titles.get(mid, "未知小说"), score)
                for mid, score in recommendations]

# 初始化推荐系统
user_cf = UserBasedCF()
user_cf.load_movie_titles()
user_cf.get_dataset()
user_cf.calc_user_sim()



@app.route('/recommend/<user_id>', methods=['GET'])
def get_recommendations(user_id):
    recommendations = user_cf.recommend_for_user(user_id)
    if not recommendations:
        return jsonify({"error": "用户不存在或数据不足"}), 404
    return jsonify({
        "user_id": user_id,
        "recommendations": [
            {"title": title, "score": float(score)}
            for title, score in recommendations
        ]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
